namespace MarketCart;

/// <summary>
/// Basket operations for the farm-market web shop: open, fill, duplicate a
/// saved basket ("reorder"), and check out into the order history.
/// </summary>
public sealed class CartService
{
    private readonly Dictionary<string, Cart> _carts = new(StringComparer.Ordinal);
    private readonly List<OrderRecord> _orders = new();
    private int _nextOrder = 1;

    public Cart Open(string cartId)
    {
        if (_carts.ContainsKey(cartId))
            throw new InvalidOperationException($"cart '{cartId}' already exists");
        var cart = new Cart(cartId);
        _carts[cartId] = cart;
        return cart;
    }

    public Cart Get(string cartId) =>
        _carts.TryGetValue(cartId, out var cart)
            ? cart
            : throw new KeyNotFoundException($"no cart '{cartId}'");

    public void AddItem(string cartId, string product, int qty, decimal unitPrice)
    {
        var cart = Get(cartId);
        var index = cart.Lines.FindIndex(l => l.Product == product);
        if (index >= 0)
        {
            var line = cart.Lines[index];
            cart.Lines[index] = line with { Qty = line.Qty + qty };
        }
        else
        {
            cart.Lines.Add(new CartLine(product, qty, unitPrice));
        }
    }

    public bool RemoveItem(string cartId, string product)
    {
        var cart = Get(cartId);
        return cart.Lines.RemoveAll(l => l.Product == product) > 0;
    }

    /// <summary>"Reorder": start a fresh basket from a saved one.</summary>
    public Cart Duplicate(string sourceId, string newId)
    {
        var source = Get(sourceId);
        if (_carts.ContainsKey(newId))
            throw new InvalidOperationException($"cart '{newId}' already exists");
        var copy = new Cart(newId) { Lines = source.Lines };
        _carts[newId] = copy;
        return copy;
    }

    /// <summary>Finalize a basket: record it in the history, empty the basket.</summary>
    public OrderRecord Checkout(string cartId)
    {
        var cart = Get(cartId);
        if (cart.Lines.Count == 0)
            throw new InvalidOperationException($"cart '{cartId}' is empty");
        var record = new OrderRecord($"ORD-{_nextOrder++}", cart.Lines, cart.Total());
        _orders.Add(record);
        cart.Lines.Clear();
        return record;
    }

    public IReadOnlyList<OrderRecord> Orders => _orders;
}
