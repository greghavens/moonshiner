using CourierDesk;

public class HandoffManifestTests
{
    static readonly string[] ThreeFiles = { "siteplan-a3.pdf", "elevations.pdf", "survey.zip" };

    static string RenderThree() => HandoffManifest.Render("Meridian Civil", "J-1042", ThreeFiles);

    [Fact]
    public void ThreeFileManifestRendersExactly()
    {
        var expected = string.Join("\n",
            "HANDOFF Meridian Civil / job J-1042",
            "Drop folder: D:\\nightly\\tickets\\reports-J-1042",
            "Read \"HANDLING NOTES.txt\" before loading.",
            "  [1] siteplan-a3.pdf",
            "  [2] elevations.pdf",
            "  [3] survey.zip",
            "Label template: {0} of 3",
            "");
        Assert.Equal(expected, RenderThree());
    }

    [Fact]
    public void SingleFileManifestRendersExactly()
    {
        var expected = string.Join("\n",
            "HANDOFF Harbor Print Co / job J-0993",
            "Drop folder: D:\\nightly\\tickets\\reports-J-0993",
            "Read \"HANDLING NOTES.txt\" before loading.",
            "  [1] banner-36in.tif",
            "Label template: {0} of 1",
            "");
        Assert.Equal(expected, HandoffManifest.Render("Harbor Print Co", "J-0993", new[] { "banner-36in.tif" }));
    }

    [Fact]
    public void ManifestIsOneLinePerRecordPlusFourHeaders()
    {
        var lines = RenderThree().Split('\n');
        Assert.Equal(4 + ThreeFiles.Length + 1, lines.Length); // + 1 empty tail after final newline
        Assert.Equal("", lines[^1]);
    }

    [Fact]
    public void NoStrayControlCharactersSneakIn()
    {
        var text = RenderThree();
        Assert.DoesNotContain('\r', text);
        Assert.DoesNotContain('\t', text);
    }

    [Fact]
    public void DropFolderLineIsAWindowsPathOnOneLine()
    {
        var lines = RenderThree().Split('\n');
        Assert.Equal(@"Drop folder: D:\nightly\tickets\reports-J-1042", lines[1]);
    }

    [Fact]
    public void HandlingNotesNameGetsExactlyOneQuoteEachSide()
    {
        var lines = RenderThree().Split('\n');
        Assert.Equal("Read \"HANDLING NOTES.txt\" before loading.", lines[2]);
    }

    [Fact]
    public void LabelTemplateKeepsItsPlaceholderForTheLabelTool()
    {
        var lines = RenderThree().Split('\n');
        Assert.Equal("Label template: {0} of 3", lines[^2]);
    }

    [Fact]
    public void EmptyCrateIsRejected()
    {
        Assert.Throws<ArgumentException>(
            () => HandoffManifest.Render("Meridian Civil", "J-1042", Array.Empty<string>()));
    }
}
