namespace MarketCart;

/// <summary>One product line in a basket.</summary>
public sealed record CartLine(string Product, int Qty, decimal UnitPrice);

/// <summary>A shopper's basket. Baskets live in the service's registry and
/// are looked up by id for every operation.</summary>
public sealed class Cart
{
    public Cart(string id) => Id = id;

    public string Id { get; }

    public List<CartLine> Lines { get; set; } = new();

    public decimal Total() => Lines.Sum(l => l.Qty * l.UnitPrice);
}

/// <summary>A finished order as it appears in the history screen.</summary>
public sealed record OrderRecord(string Id, IReadOnlyList<CartLine> Lines, decimal Total);
