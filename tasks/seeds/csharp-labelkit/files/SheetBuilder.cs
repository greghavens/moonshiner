namespace LabelKit;

/// <summary>Builds whole printer sheets from stock lines ("SKU|Title").</summary>
public static class SheetBuilder
{
    /// <summary>One rendered label per stock line, all at the same width.</summary>
    public static List<string> BuildSheet(IEnumerable<string> stockLines, int width)
    {
        var labels = new List<string>();
        foreach (var line in stockLines)
        {
            var parts = line.Split('|');
            var spec = LabelSpec.Make(parts[0].Trim(), parts[1].Trim(), width);
            labels.Add(Renderer.Render(spec));
        }
        return labels;
    }

    /// <summary>Render a single ad-hoc label.</summary>
    public static string BuildOne(string sku, string title, int width)
        => Renderer.RenderRaw(sku, title, width);

    /// <summary>Goods-in checking line, e.g. "SKU-88 check 5".</summary>
    public static string CheckLine(string sku)
        => sku + " check " + LabelCodes.Checksum(sku);

    /// <summary>Aisle banner printed above that aisle's labels.</summary>
    public static string AisleHeader(string aisle, int width, string binHint)
    {
        return ("== " + aisle + " ").PadRight(width, '=');
    }

    /// <summary>Trailer line for the sheet.</summary>
    internal static string SheetFooter(int labelCount, string operatorName)
        => $"-- {labelCount} labels --";
}
