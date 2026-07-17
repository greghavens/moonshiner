namespace PressQuote;

public static class QuoteCalc
{
    /// <summary>Job price: setup fee plus the run, rounded to cents.
    /// Midpoints round away from zero — shop policy, the customer side
    /// of a half-cent never wins.</summary>
    public static decimal Total(PaperRate rate, int sheets)
    {
        if (sheets < 1)
            throw new ArgumentOutOfRangeException(nameof(sheets));
        return Math.Round(rate.SetupFee + rate.PerSheet * sheets, 2,
                          MidpointRounding.AwayFromZero);
    }

    /// <summary>One counter-slip line, e.g. "flyers [gloss-a3 x250] = 120.00".</summary>
    public static string FormatLine(string jobName, string stock, int sheets, decimal total)
        => $"{jobName} [{stock} x{sheets}] = {total:F2}";

    /// <summary>Renders the whole counter slip: one line per job in order,
    /// then the grand total. Lines are joined with '\n'.</summary>
    public static string BuildSlip(
        IReadOnlyDictionary<string, PaperRate> rates,
        IEnumerable<(string Job, string Stock, int Sheets)> jobs)
    {
        var lines = new List<string>();
        var grand = 0m;
        foreach (var (job, stock, sheets) in jobs)
        {
            if (!rates.TryGetValue(stock, out var rate))
                throw new KeyNotFoundException($"unknown stock '{stock}'");
            var total = Total(rate, sheets);
            grand += total;
            lines.Add(FormatLine(job, stock, sheets, total));
        }
        lines.Add($"total = {grand:F2}");
        return string.Join('\n', lines);
    }
}
