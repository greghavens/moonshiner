namespace LabelKit;

public class LabelKitTests
{
    [Fact]
    public void Mod10SkipsNonDigitsAndWeightsFromTheRight()
    {
        Assert.Equal(5, LabelCodes.Mod10("SKU-7141"));
        Assert.Equal(5, LabelCodes.Mod10("SKU-88"));
        Assert.Equal(4, LabelCodes.Mod10("BOLT-6"));
        Assert.Equal(0, LabelCodes.Mod10("NO-DIGITS"));
    }

    [Fact]
    public void RenderPadsShortLabelsWithDots()
    {
        var spec = new LabelSpec { Sku = "SKU-88", Title = "Hinge" };
        Assert.Equal("SKU-88 Hinge............ c/d 5", Renderer.Render(spec));
    }

    [Fact]
    public void RenderTruncatesLongLabelsAtTheWidth()
    {
        var spec = new LabelSpec { Sku = "SKU-7141", Title = "Wood screws 40mm" };
        Assert.Equal("SKU-7141 Wood screws 40m c/d 5", Renderer.Render(spec));
    }

    [Fact]
    public void RenderLeavesExactWidthTextAlone()
    {
        var spec = new LabelSpec { Sku = "SKU-88", Title = "Hinge", Width = 12 };
        Assert.Equal("SKU-88 Hinge c/d 5", Renderer.Render(spec));
    }

    [Fact]
    public void BuildSheetRendersOneLabelPerStockLine()
    {
        var sheet = SheetBuilder.BuildSheet(
            new[] { "SKU-7141|Wood screws 40mm", "SKU-88|Hinge" }, 24);
        Assert.Equal(new List<string>
        {
            "SKU-7141 Wood screws 40m c/d 5",
            "SKU-88 Hinge............ c/d 5",
        }, sheet);
    }

    [Fact]
    public void BuildSheetTrimsPaddedStockLines()
    {
        var sheet = SheetBuilder.BuildSheet(new[] { " SKU-88 | Hinge " }, 24);
        Assert.Equal(new List<string> { "SKU-88 Hinge............ c/d 5" }, sheet);
    }

    [Fact]
    public void BuildOneRendersAnAdHocLabel()
    {
        Assert.Equal("BOLT-6 Coach bol c/d 4", SheetBuilder.BuildOne("BOLT-6", "Coach bolt", 16));
    }

    [Fact]
    public void CheckLineCarriesTheCheckDigit()
    {
        Assert.Equal("SKU-88 check 5", SheetBuilder.CheckLine("SKU-88"));
        Assert.Equal("BOLT-6 check 4", SheetBuilder.CheckLine("BOLT-6"));
    }

    [Fact]
    public void AisleHeaderIsPaddedWithRules()
    {
        Assert.Equal("== A3 ======", SheetBuilder.AisleHeader("A3", 12));
        Assert.Equal("== Fasteners ===", SheetBuilder.AisleHeader("Fasteners", 16));
    }

    [Fact]
    public void SheetFooterCountsLabels()
    {
        Assert.Equal("-- 3 labels --", SheetBuilder.SheetFooter(3));
        Assert.Equal("-- 0 labels --", SheetBuilder.SheetFooter(0));
    }
}
