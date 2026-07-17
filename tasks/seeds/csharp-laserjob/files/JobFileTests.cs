using System.Globalization;
using System.Text.Json;

namespace LaserJob;

public class WireFormatTests
{
    [Fact]
    public void CutStepWireFormatIsExact()
    {
        var json = JobFile.Serialize(new JobStep[] { new CutStep("outline.svg", new Speed(120m), 2) });

        Assert.Equal("[{\"op\":\"cut\",\"path\":\"outline.svg\",\"feed\":\"120mm/s\",\"passes\":2}]", json);
    }

    [Fact]
    public void EngraveAndPauseWireFormatsAreExact()
    {
        var json = JobFile.Serialize(new JobStep[]
        {
            new EngraveStep("logo.png", 300, new Speed(85.5m)),
            new PauseStep("flip the sheet"),
        });

        Assert.Equal(
            "[{\"op\":\"engrave\",\"path\":\"logo.png\",\"dpi\":300,\"feed\":\"85.5mm/s\"}," +
            "{\"op\":\"pause\",\"message\":\"flip the sheet\"}]",
            json);
    }

    [Fact]
    public void EmptyJobIsAnEmptyArrayBothWays()
    {
        Assert.Equal("[]", JobFile.Serialize(Array.Empty<JobStep>()));
        Assert.Empty(JobFile.Deserialize("[]"));
    }

    [Fact]
    public void SpeedStaysInvariantUnderACommaDecimalCulture()
    {
        var before = CultureInfo.CurrentCulture;
        try
        {
            CultureInfo.CurrentCulture = new CultureInfo("de-DE");
            var json = JobFile.Serialize(new JobStep[] { new CutStep("rim.svg", new Speed(85.5m), 1) });
            Assert.Contains("\"feed\":\"85.5mm/s\"", json);

            var steps = JobFile.Deserialize("[{\"op\":\"cut\",\"path\":\"rim.svg\",\"feed\":\"85.5mm/s\",\"passes\":1}]");
            Assert.Equal(new Speed(85.5m), ((CutStep)steps[0]).Feed);
        }
        finally
        {
            CultureInfo.CurrentCulture = before;
        }
    }
}

public class ReadBackTests
{
    [Fact]
    public void DeserializeRebuildsTypedSteps()
    {
        var steps = JobFile.Deserialize(
            "[{\"op\":\"cut\",\"path\":\"outline.svg\",\"feed\":\"120mm/s\",\"passes\":2}," +
            "{\"op\":\"engrave\",\"path\":\"logo.png\",\"dpi\":300,\"feed\":\"85.5mm/s\"}," +
            "{\"op\":\"pause\",\"message\":\"flip the sheet\"}]");

        Assert.Equal(3, steps.Count);
        var cut = Assert.IsType<CutStep>(steps[0]);
        Assert.Equal("outline.svg", cut.Path);
        Assert.Equal(new Speed(120m), cut.Feed);
        Assert.Equal(2, cut.Passes);
        var engrave = Assert.IsType<EngraveStep>(steps[1]);
        Assert.Equal(300, engrave.Dpi);
        Assert.Equal(new Speed(85.5m), engrave.Feed);
        var pause = Assert.IsType<PauseStep>(steps[2]);
        Assert.Equal("flip the sheet", pause.Message);
    }

    [Fact]
    public void RoundTripIsExactInBothDirections()
    {
        var original = new JobStep[]
        {
            new CutStep("outline.svg", new Speed(120m), 2),
            new PauseStep("flip the sheet"),
            new EngraveStep("logo.png", 600, new Speed(42.25m)),
        };

        var wire = JobFile.Serialize(original);
        Assert.Equal(original, JobFile.Deserialize(wire));      // objects -> wire -> same objects
        Assert.Equal(wire, JobFile.Serialize(JobFile.Deserialize(wire)));  // wire -> objects -> same bytes
    }
}

public class RejectionTests
{
    [Fact]
    public void UnknownOpIsRejected()
    {
        var ex = Assert.Throws<JsonException>(
            () => JobFile.Deserialize("[{\"op\":\"raster\",\"path\":\"x.svg\"}]"));
        Assert.Contains("raster", ex.Message);
    }

    [Fact]
    public void MissingOpIsRejected()
    {
        var ex = Assert.Throws<NotSupportedException>(
            () => JobFile.Deserialize("[{\"path\":\"x.svg\",\"passes\":1}]"));
        Assert.Contains("type discriminator", ex.Message);
    }

    [Fact]
    public void MalformedSpeedStringIsRejectedWithTheOffendingText()
    {
        var ex = Assert.Throws<JsonException>(
            () => JobFile.Deserialize("[{\"op\":\"cut\",\"path\":\"x.svg\",\"feed\":\"fast\",\"passes\":1}]"));
        Assert.Equal("invalid speed 'fast'", ex.Message);

        var units = Assert.Throws<JsonException>(
            () => JobFile.Deserialize("[{\"op\":\"cut\",\"path\":\"x.svg\",\"feed\":\"120in/s\",\"passes\":1}]"));
        Assert.Equal("invalid speed '120in/s'", units.Message);
    }

    [Fact]
    public void NumericSpeedIsRejectedAsAShapeError()
    {
        var ex = Assert.Throws<JsonException>(
            () => JobFile.Deserialize("[{\"op\":\"cut\",\"path\":\"x.svg\",\"feed\":120,\"passes\":1}]"));
        Assert.Equal("speed must be a string like '120mm/s'", ex.Message);
    }
}
