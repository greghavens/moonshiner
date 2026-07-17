namespace LabelKit;

/// <summary>Layout for one shelf-edge label.</summary>
public class LabelSpec
{
    public required string Sku { get; init; }
    public required string Title { get; init; }
    public int Width { get; init; } = 24;

    [Obsolete("build a LabelSpec with object-initializer properties instead")]
    public static LabelSpec Make(string sku, string title, int width)
        => new() { Sku = sku, Title = title, Width = width };
}

/// <summary>Check-digit helpers for SKU codes.</summary>
public static class LabelCodes
{
    /// <summary>
    /// Mod-10 check digit over the digits in a SKU (non-digits are skipped):
    /// weights 1,2,1,2… from the rightmost digit, doubled digits fold above 9.
    /// </summary>
    public static int Mod10(string sku)
    {
        int sum = 0;
        int position = 0;
        for (int i = sku.Length - 1; i >= 0; i--)
        {
            if (!char.IsAsciiDigit(sku[i]))
            {
                continue;
            }
            int d = sku[i] - '0';
            if (position % 2 == 1)
            {
                d *= 2;
                if (d > 9)
                {
                    d -= 9;
                }
            }
            sum += d;
            position++;
        }
        return (10 - sum % 10) % 10;
    }

    [Obsolete("use Mod10")]
    public static int Checksum(string sku) => Mod10(sku);
}

/// <summary>Turns specs into fixed-width printer lines.</summary>
public static class Renderer
{
    /// <summary>
    /// One printer line: "SKU Title" truncated or dot-padded to the spec width,
    /// then the check digit, e.g. "SKU-88 Hinge............ c/d 5".
    /// </summary>
    public static string Render(LabelSpec spec)
    {
        string text = spec.Sku + " " + spec.Title;
        if (text.Length > spec.Width)
        {
            text = text.Substring(0, spec.Width);
        }
        else
        {
            text = text.PadRight(spec.Width, '.');
        }
        return text + " c/d " + LabelCodes.Mod10(spec.Sku);
    }

    [Obsolete("use Render(LabelSpec)")]
    public static string RenderRaw(string sku, string title, int width)
        => Render(new LabelSpec { Sku = sku, Title = title, Width = width });
}
