namespace WireDepot;

/// <summary>
/// Acceptance contract for branch intake, pricing, and picking. Every
/// expected money figure was recomputed by hand from the catalog list
/// prices and the account's contract basis points (shares round half up).
/// </summary>
public class FulfillmentTests
{
    private static (PricingService Pricing, FulfillmentService Fulfillment) NewBranch()
    {
        var catalog = new List<CatalogItem>
        {
            new("THHN-12-500", "12 AWG THHN copper, 500 ft spool", 8950),
            new("EMT-075-10", "3/4 in EMT conduit, 10 ft stick", 685),
            new("BRK-220-2P", "220 A two-pole breaker", 12400),
        };
        for (int i = 1; i <= 30; i++)
        {
            string sku = "FTG-" + i.ToString("000", System.Globalization.CultureInfo.InvariantCulture);
            catalog.Add(new CatalogItem(sku, $"EMT fitting style {i}", 145));
        }
        var customers = new List<Customer>
        {
            new("C-HALV", "Halvorsen Electric", 1200),
            new("C-BAYS", "Bayside Controls", 0),
        };
        var pricing = new PricingService(catalog, customers);
        return (pricing, new FulfillmentService(pricing));
    }

    private static List<(string Sku, int Quantity)> Fittings(int from, int count)
    {
        var items = new List<(string, int)>();
        for (int i = from; i < from + count; i++)
        {
            string sku = "FTG-" + i.ToString("000", System.Globalization.CultureInfo.InvariantCulture);
            items.Add((sku, 1));
        }
        return items;
    }

    [Fact]
    public void CatalogLookupIsCaseInsensitive()
    {
        var (pricing, _) = NewBranch();
        Assert.True(pricing.TryFindItem("thhn-12-500", out var item));
        Assert.Equal("THHN-12-500", item.Sku);
        Assert.Equal(8950L, item.ListUnitCents);
    }

    [Fact]
    public void ListAccountBilledAtList()
    {
        var (pricing, fulfillment) = NewBranch();
        var order = fulfillment.PlaceOrder("C-BAYS", new[] { ("EMT-075-10", 10) });
        Assert.Equal(6850L, pricing.OrderTotalCents(order));
    }

    [Fact]
    public void ContractAccountDiscountedOncePerLine()
    {
        var (pricing, fulfillment) = NewBranch();
        // 4*8950 = 35800 - 12% (4296) = 31504; 40*685 = 27400 - 12% (3288) = 24112
        var order = fulfillment.PlaceOrder("C-HALV",
            new[] { ("THHN-12-500", 4), ("EMT-075-10", 40) });
        Assert.Equal(55616L, pricing.OrderTotalCents(order));
    }

    [Fact]
    public void QuoteConversionBillsSameAsDirect()
    {
        var (pricing, fulfillment) = NewBranch();
        var direct = fulfillment.PlaceOrder("C-HALV",
            new[] { ("THHN-12-500", 4), ("EMT-075-10", 40) });
        var quoted = fulfillment.ConvertQuote(new Quote("Q-7741", "C-HALV",
            new[] { ("THHN-12-500", 4), ("EMT-075-10", 40) }));
        Assert.Equal(55616L, pricing.OrderTotalCents(direct));
        Assert.Equal(55616L, pricing.OrderTotalCents(quoted));
    }

    [Fact]
    public void SameCaseDuplicateLinesMergeOntoOneLine()
    {
        var (_, fulfillment) = NewBranch();
        var order = fulfillment.PlaceOrder("C-BAYS",
            new[] { ("EMT-075-10", 3), ("EMT-075-10", 2) });
        var line = Assert.Single(order.Lines);
        Assert.Equal("EMT-075-10", line.Sku);
        Assert.Equal(5, line.Quantity);
    }

    [Fact]
    public void CrossCaseDuplicateLinesMergeOntoOneLine()
    {
        var (_, fulfillment) = NewBranch();
        var order = fulfillment.PlaceOrder("C-BAYS",
            new[] { ("BRK-220-2P", 4), ("brk-220-2p", 2) });
        var line = Assert.Single(order.Lines);
        Assert.Equal("BRK-220-2P", line.Sku);
        Assert.Equal(6, line.Quantity);
    }

    [Fact]
    public void PickBatchesRespectCartCapacity()
    {
        var (_, fulfillment) = NewBranch();
        fulfillment.PlaceOrder("C-BAYS", Fittings(1, 10));   // SO-1000
        fulfillment.PlaceOrder("C-BAYS", Fittings(11, 9));   // SO-1001
        fulfillment.PlaceOrder("C-BAYS", Fittings(20, 7));   // SO-1002
        var batches = fulfillment.BuildPickBatches(12);
        Assert.Equal(3, batches.Count);
        Assert.Equal(new[] { 12, 12, 2 }, batches.Select(b => b.Lines.Count).ToArray());
        Assert.All(batches, b => Assert.True(b.Lines.Count <= 12));
        Assert.Equal(new[] { 1, 2, 3 }, batches.Select(b => b.Seq).ToArray());
        Assert.Equal(("SO-1000", "FTG-001", 1), batches[0].Lines[0]);
        Assert.Equal(
            new[] { ("SO-1002", "FTG-025", 1), ("SO-1002", "FTG-026", 1) },
            batches[2].Lines.ToArray());
    }

    [Fact]
    public void PickBatchOverflowStartsNewCart()
    {
        var (_, fulfillment) = NewBranch();
        fulfillment.PlaceOrder("C-BAYS", Fittings(1, 5));
        var batches = fulfillment.BuildPickBatches(4);
        Assert.Equal(2, batches.Count);
        Assert.Equal(4, batches[0].Lines.Count);
        var last = Assert.Single(batches[1].Lines);
        Assert.Equal(("SO-1000", "FTG-005", 1), last);
    }

    [Fact]
    public void PickBatchExactCapacityIsOneCart()
    {
        var (_, fulfillment) = NewBranch();
        fulfillment.PlaceOrder("C-BAYS", Fittings(1, 4));
        var batches = fulfillment.BuildPickBatches(4);
        var batch = Assert.Single(batches);
        Assert.Equal(4, batch.Lines.Count);
    }

    [Fact]
    public void DailySummaryOrderedBySkuOrdinal()
    {
        var (_, fulfillment) = NewBranch();
        fulfillment.PlaceOrder("C-BAYS",
            new[] { ("EMT-075-10", 3), ("BRK-220-2P", 1), ("THHN-12-500", 2) });
        fulfillment.PlaceOrder("C-HALV", new[] { ("EMT-075-10", 2) });
        Assert.Equal(
            new[] { ("BRK-220-2P", 1), ("EMT-075-10", 5), ("THHN-12-500", 2) },
            fulfillment.DailySkuSummary().ToArray());
    }

    [Fact]
    public void RejectsUnknownSkuCustomerAndBadInput()
    {
        var (_, fulfillment) = NewBranch();
        Assert.Throws<KeyNotFoundException>(
            () => fulfillment.PlaceOrder("C-BAYS", new[] { ("ZZZ-000", 1) }));
        Assert.Throws<KeyNotFoundException>(
            () => fulfillment.PlaceOrder("C-NOPE", new[] { ("EMT-075-10", 1) }));
        Assert.Throws<ArgumentException>(
            () => fulfillment.PlaceOrder("C-BAYS", new[] { ("EMT-075-10", 0) }));
        Assert.Throws<ArgumentException>(
            () => fulfillment.PlaceOrder("C-BAYS", Array.Empty<(string, int)>()));
        Assert.Throws<KeyNotFoundException>(() => fulfillment.GetOrder("SO-9999"));
    }

    [Fact]
    public void EndToEndDayReconciliation()
    {
        var (pricing, fulfillment) = NewBranch();
        var direct = fulfillment.PlaceOrder("C-HALV",
            new[] { ("THHN-12-500", 4), ("EMT-075-10", 40) });                 // SO-1000
        var quoted = fulfillment.ConvertQuote(new Quote("Q-7741", "C-HALV",
            new[] { ("THHN-12-500", 4), ("EMT-075-10", 40) }));                // SO-1001
        var walkIn = fulfillment.PlaceOrder("C-BAYS",
            new[] { ("BRK-220-2P", 4), ("brk-220-2p", 2), ("EMT-075-10", 10) }); // SO-1002

        Assert.Equal(55616L, pricing.OrderTotalCents(direct));
        Assert.Equal(55616L, pricing.OrderTotalCents(quoted));
        Assert.Equal(81250L, pricing.OrderTotalCents(walkIn));

        var batches = fulfillment.BuildPickBatches(4);
        Assert.Equal(2, batches.Count);
        Assert.Equal(new[] { 4, 2 }, batches.Select(b => b.Lines.Count).ToArray());
        Assert.Equal(
            new[] { ("SO-1002", "BRK-220-2P", 6), ("SO-1002", "EMT-075-10", 10) },
            batches[1].Lines.ToArray());

        Assert.Equal(
            new[] { ("BRK-220-2P", 6), ("EMT-075-10", 90), ("THHN-12-500", 8) },
            fulfillment.DailySkuSummary().ToArray());
    }
}
