namespace WireDepot;

/// <summary>Catalog entry for a stocked item; all prices are integer cents.</summary>
public sealed record CatalogItem(string Sku, string Description, long ListUnitCents);

/// <summary>
/// A wholesale account. <see cref="ContractBps"/> is the negotiated
/// discount off list in basis points (0 = billed at list).
/// </summary>
public sealed record Customer(string Id, string Name, int ContractBps);

/// <summary>A quote a rep sent to an account; conversion turns it into an order.</summary>
public sealed record Quote(string QuoteId, string CustomerId, IReadOnlyList<(string Sku, int Quantity)> Items);

/// <summary>One order line. <see cref="Sku"/> is the catalog's canonical casing.</summary>
public sealed class OrderLine
{
    public OrderLine(string sku, int quantity, long unitCents)
    {
        Sku = sku;
        Quantity = quantity;
        UnitCents = unitCents;
    }

    public string Sku { get; }
    public int Quantity { get; internal set; }
    public long UnitCents { get; }
}

/// <summary>A sales order as accepted at intake.</summary>
public sealed class Order
{
    public Order(string id, string customerId)
    {
        Id = id;
        CustomerId = customerId;
    }

    public string Id { get; }
    public string CustomerId { get; }
    public List<OrderLine> Lines { get; } = new();
}

/// <summary>One picking-cart batch: pick lines in intake order.</summary>
public sealed class PickBatch
{
    public PickBatch(int seq)
    {
        Seq = seq;
    }

    public int Seq { get; }
    public List<(string OrderId, string Sku, int Quantity)> Lines { get; } = new();
}
