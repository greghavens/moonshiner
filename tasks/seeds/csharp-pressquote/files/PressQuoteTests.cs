using System.Globalization;

namespace PressQuote;

public class PressQuoteTests
{
    private const string Sheet = """
        # spring price list — office spreadsheet export
        gloss-a3;0.42;15.00

        matte-a4;0.18;9.50
        card-a3;0.95;22.00
        vellum-a5;0.073;5.00
        """;

    public PressQuoteTests()
    {
        // The QA rig mirrors the front-counter machines' OS regional
        // settings so the suite sees exactly what the shop sees.
        CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("de-DE");
    }

    [Fact]
    public void RateSheetValuesComeThroughExactly()
    {
        var rates = RateSheet.Parse(Sheet);

        Assert.Equal(4, rates.Count);
        Assert.Equal(new PaperRate("gloss-a3", 0.42m, 15.00m), rates["gloss-a3"]);
        Assert.Equal(new PaperRate("matte-a4", 0.18m, 9.50m), rates["matte-a4"]);
        Assert.Equal(0.073m, rates["vellum-a5"].PerSheet);
    }

    [Fact]
    public void LaterDuplicateLinesWin()
    {
        var rates = RateSheet.Parse("gloss-a3;0.42;15.00\ngloss-a3;0.40;15.00");
        Assert.Equal(0.40m, rates["gloss-a3"].PerSheet);
    }

    [Fact]
    public void MalformedLinesAreReportedWithTheirLineNumber()
    {
        var ex = Assert.Throws<FormatException>(
            () => RateSheet.Parse("gloss-a3;0.42;15.00\nmatte-a4;0.18"));
        Assert.Equal("line 2: expected 3 fields, got 2", ex.Message);
    }

    [Fact]
    public void TotalIsSetupPlusRunRoundedToCents()
    {
        var gloss = new PaperRate("gloss-a3", 0.42m, 15.00m);
        Assert.Equal(120.00m, QuoteCalc.Total(gloss, 250));
        Assert.Equal(15.42m, QuoteCalc.Total(gloss, 1));
    }

    [Fact]
    public void HalfCentsRoundAwayFromZero()
    {
        var vellum = new PaperRate("vellum-a5", 0.073m, 5.00m);
        // 5.00 + 0.365 = 5.365 -> 5.37, not banker's 5.36
        Assert.Equal(5.37m, QuoteCalc.Total(vellum, 5));
    }

    [Fact]
    public void SlipLinesAlwaysUseDotDecimals()
    {
        var line = QuoteCalc.FormatLine("flyers", "gloss-a3", 250, 120.00m);
        Assert.Equal("flyers [gloss-a3 x250] = 120.00", line);
    }

    [Fact]
    public void WholeSlipEndToEndFromTheSpreadsheetExport()
    {
        var rates = RateSheet.Parse(Sheet);
        var slip = QuoteCalc.BuildSlip(rates, new[]
        {
            ("flyers", "gloss-a3", 250),
            ("menus", "matte-a4", 120),
            ("tags", "card-a3", 40),
        });

        Assert.Equal(
            "flyers [gloss-a3 x250] = 120.00\n" +
            "menus [matte-a4 x120] = 31.10\n" +
            "tags [card-a3 x40] = 60.00\n" +
            "total = 211.10",
            slip);
    }

    [Fact]
    public void UnknownStockOnAJobIsRejectedByName()
    {
        var rates = RateSheet.Parse(Sheet);
        var ex = Assert.Throws<KeyNotFoundException>(
            () => QuoteCalc.BuildSlip(rates, new[] { ("banners", "silk-a2", 10) }));
        Assert.Equal("unknown stock 'silk-a2'", ex.Message);
    }
}
