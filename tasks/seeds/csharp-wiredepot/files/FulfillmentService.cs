namespace WireDepot;

/// <summary>
/// Order intake and warehouse picking for the branch.
///
/// Intake validates every SKU against the catalog and merges duplicate
/// SKUs onto one line — vendor punchout systems disagree on casing, so SKU
/// matching is case-insensitive by exact characters. Lines are stored at
/// list price; contract discounts are applied exactly once, by
/// <see cref="PricingService.OrderTotalCents"/>, when the order is billed.
/// Picking splits the day's lines into cart batches of at most the cart's
/// capacity, in intake order.
/// </summary>
public sealed class FulfillmentService
{
    private readonly PricingService _pricing;
    private readonly Dictionary<string, Order> _orders = new(StringComparer.Ordinal);
    private readonly List<string> _intake = new();
    private int _nextOrder = 1000;

    public FulfillmentService(PricingService pricing)
    {
        _pricing = pricing;
    }

    /// <summary>Accept an order keyed in directly by the desk.</summary>
    public Order PlaceOrder(string customerId, IEnumerable<(string Sku, int Quantity)> items)
    {
        _pricing.GetCustomer(customerId);
        var order = new Order($"SO-{_nextOrder++}", customerId);
        foreach (var line in MergeItems(items))
            order.Lines.Add(line);
        return Accept(order);
    }

    /// <summary>Honor a rep's quote by converting it into an order.</summary>
    public Order ConvertQuote(Quote quote)
    {
        var customer = _pricing.GetCustomer(quote.CustomerId);
        var order = new Order($"SO-{_nextOrder++}", quote.CustomerId);
        foreach (var line in MergeItems(quote.Items))
        {
            // Quotes are honored at the price promised on the quote, so
            // carry the account's contract price onto the order lines.
            long unit = line.UnitCents;
            if (customer.ContractBps > 0 && _pricing.TryFindItem(line.Sku, out var item))
                unit = _pricing.ContractUnitCents(customer, item);
            order.Lines.Add(new OrderLine(line.Sku, line.Quantity, unit));
        }
        return Accept(order);
    }

    public Order GetOrder(string id) =>
        _orders.TryGetValue(id, out var order)
            ? order
            : throw new KeyNotFoundException($"unknown order: {id}");

    /// <summary>
    /// Split the day's pick lines (orders in intake order, lines in line
    /// order) into cart batches of at most <paramref name="cartCapacity"/> lines.
    /// </summary>
    public List<PickBatch> BuildPickBatches(int cartCapacity)
    {
        if (cartCapacity <= 0)
            throw new ArgumentOutOfRangeException(nameof(cartCapacity));
        var batches = new List<PickBatch>();
        var current = new PickBatch(1);
        foreach (var orderId in _intake)
        {
            var order = _orders[orderId];
            foreach (var line in order.Lines)
            {
                current.Lines.Add((order.Id, line.Sku, line.Quantity));
                if (current.Lines.Count > cartCapacity)
                {
                    batches.Add(current);
                    current = new PickBatch(batches.Count + 1);
                }
            }
        }
        if (current.Lines.Count > 0)
            batches.Add(current);
        return batches;
    }

    /// <summary>Total quantity per SKU across the day, ordered by SKU (ordinal).</summary>
    public List<(string Sku, int Quantity)> DailySkuSummary()
    {
        var totals = new Dictionary<string, int>(StringComparer.Ordinal);
        foreach (var orderId in _intake)
            foreach (var line in _orders[orderId].Lines)
                totals[line.Sku] = totals.GetValueOrDefault(line.Sku) + line.Quantity;
        return totals
            .OrderBy(kv => kv.Key, StringComparer.Ordinal)
            .Select(kv => (kv.Key, kv.Value))
            .ToList();
    }

    private Order Accept(Order order)
    {
        if (order.Lines.Count == 0)
            throw new ArgumentException($"order {order.Id} has no lines");
        _orders.Add(order.Id, order);
        _intake.Add(order.Id);
        return order;
    }

    private List<OrderLine> MergeItems(IEnumerable<(string Sku, int Quantity)> items)
    {
        var merged = new Dictionary<string, OrderLine>(StringComparer.InvariantCulture);
        var firstSeen = new List<string>();
        foreach (var (sku, quantity) in items)
        {
            if (quantity <= 0)
                throw new ArgumentException($"bad quantity {quantity} for {sku}");
            if (!_pricing.TryFindItem(sku, out var item))
                throw new KeyNotFoundException($"unknown sku: {sku}");
            if (merged.TryGetValue(sku, out var existing))
            {
                existing.Quantity += quantity;
            }
            else
            {
                merged.Add(sku, new OrderLine(item.Sku, quantity, item.ListUnitCents));
                firstSeen.Add(sku);
            }
        }
        return firstSeen.Select(key => merged[key]).ToList();
    }
}
