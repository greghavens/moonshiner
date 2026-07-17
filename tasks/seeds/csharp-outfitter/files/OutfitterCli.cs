using System.Globalization;

namespace Outfitter;

/// <summary>
/// Line-oriented front-desk console for the outfitter's boat barn. Each call
/// to <see cref="Run"/> takes one command line and returns the text the desk
/// terminal prints.
/// </summary>
public sealed class OutfitterCli
{
    private readonly StockRoom _stock = new();

    public string Run(string line)
    {
        var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length == 0)
            return "error: empty command";
        return parts[0] switch
        {
            "receive" => Receive(parts),
            "sell" => Sell(parts),
            "stock" => Stock(),
            _ => $"error: unknown command '{parts[0]}'",
        };
    }

    private string Receive(string[] parts)
    {
        if (parts.Length != 3)
            return "error: usage: receive <sku> <qty>";
        if (!TryQty(parts[2], out var qty))
            return "error: quantity must be a positive integer";
        _stock.Receive(parts[1], qty);
        return $"ok: {parts[1]} now {_stock.OnHand(parts[1])}";
    }

    private string Sell(string[] parts)
    {
        if (parts.Length != 3)
            return "error: usage: sell <sku> <qty>";
        if (!TryQty(parts[2], out var qty))
            return "error: quantity must be a positive integer";
        var sku = parts[1];
        if (!_stock.Knows(sku))
            return $"error: unknown sku '{sku}'";
        if (_stock.OnHand(sku) < qty)
            return $"error: only {_stock.OnHand(sku)} of {sku} available";
        _stock.Remove(sku, qty);
        return $"ok: {sku} now {_stock.OnHand(sku)}";
    }

    private string Stock()
    {
        var rows = _stock.Snapshot();
        return rows.Count == 0
            ? "(empty)"
            : string.Join('\n', rows.Select(r => $"{r.Sku} {r.Qty}"));
    }

    private static bool TryQty(string raw, out int qty) =>
        int.TryParse(raw, NumberStyles.None, CultureInfo.InvariantCulture, out qty) && qty > 0;
}
