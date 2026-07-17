namespace Outfitter;

/// <summary>On-hand counts for the boat barn, keyed by SKU.</summary>
public sealed class StockRoom
{
    private readonly Dictionary<string, int> _onHand = new(StringComparer.Ordinal);

    public bool Knows(string sku) => _onHand.ContainsKey(sku);

    public int OnHand(string sku) => _onHand.TryGetValue(sku, out var qty) ? qty : 0;

    public void Receive(string sku, int qty) => _onHand[sku] = OnHand(sku) + qty;

    public void Remove(string sku, int qty) => _onHand[sku] = OnHand(sku) - qty;

    /// <summary>All SKUs with their counts, ordinal-sorted by SKU.</summary>
    public IReadOnlyList<(string Sku, int Qty)> Snapshot() =>
        _onHand.OrderBy(kv => kv.Key, StringComparer.Ordinal)
               .Select(kv => (kv.Key, kv.Value))
               .ToList();
}
