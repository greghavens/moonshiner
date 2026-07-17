namespace PressQuote;

/// <summary>Per-stock pricing: cost per sheet plus a one-off setup fee.</summary>
public sealed record PaperRate(string Stock, decimal PerSheet, decimal SetupFee);

public static class RateSheet
{
    /// <summary>
    /// Parses the office price list. One entry per line:
    ///
    ///     stock;per_sheet;setup_fee
    ///     gloss-a3;0.42;15.00
    ///
    /// Blank lines and '#' comments are ignored. Later lines win when a
    /// stock is listed twice (the sheet is edited top-down all season).
    /// </summary>
    public static IReadOnlyDictionary<string, PaperRate> Parse(string text)
    {
        var rates = new Dictionary<string, PaperRate>();
        var lineNo = 0;
        foreach (var raw in text.Split('\n'))
        {
            lineNo++;
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#'))
                continue;

            var parts = line.Split(';');
            if (parts.Length != 3)
                throw new FormatException($"line {lineNo}: expected 3 fields, got {parts.Length}");

            var stock = parts[0].Trim();
            if (stock.Length == 0)
                throw new FormatException($"line {lineNo}: missing stock name");

            rates[stock] = new PaperRate(
                stock,
                decimal.Parse(parts[1].Trim()),
                decimal.Parse(parts[2].Trim()));
        }
        return rates;
    }
}
