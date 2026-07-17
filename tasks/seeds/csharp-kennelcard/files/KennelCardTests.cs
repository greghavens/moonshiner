using PinesKennel;

public class KennelCardTests
{
    private static Booking Biscuit(bool hold) => new(
        Pet: "Biscuit",
        Owner: "R. Okafor",
        Run: "7",
        Food: "kibble",
        MealCups: 1.5,
        Meds: new[] { "ear drops after breakfast" },
        Hold: hold);

    [Fact]
    public void CardForAHoldStayEndsWithTheBanner()
    {
        var expected =
            "RUN 7 — Biscuit (R. Okafor)\n" +
            "kibble, 1.5 cups, morning and evening\n" +
            "MED: ear drops after breakfast\n" +
            "** DO NOT RELEASE — the desk marked this stay \"HOLD\"";
        Assert.Equal(expected, KennelCard.Render(Biscuit(hold: true)));
    }

    [Fact]
    public void CardForANormalStayHasNoBanner()
    {
        var card = KennelCard.Render(Biscuit(hold: false));
        Assert.DoesNotContain("DO NOT RELEASE", card);
        Assert.DoesNotContain("HOLD", card);
    }

    [Fact]
    public void BannerQuotesTheWordHoldExactly()
    {
        var card = KennelCard.Render(Biscuit(hold: true));
        Assert.Contains("\"HOLD\"", card);
        Assert.EndsWith("\"HOLD\"", card);
    }

    [Fact]
    public void FeedLineUsesOneDecimalRegardlessOfLocale()
    {
        var booking = Biscuit(hold: false) with { Food = "wet food", MealCups = 2.0 };
        var card = KennelCard.Render(booking);
        Assert.Contains("wet food, 2.0 cups, morning and evening", card);
    }

    [Fact]
    public void EveryMedGetsItsOwnLineInOrder()
    {
        var booking = Biscuit(hold: false) with
        {
            Meds = new[] { "half tablet with dinner", "joint chew at noon" }
        };
        var lines = KennelCard.Render(booking).Split('\n');
        Assert.Equal("MED: half tablet with dinner", lines[2]);
        Assert.Equal("MED: joint chew at noon", lines[3]);
    }

    [Fact]
    public void NoMedsMeansNoMedLines()
    {
        var booking = Biscuit(hold: false) with { Meds = Array.Empty<string>() };
        var lines = KennelCard.Render(booking).Split('\n');
        Assert.Equal(2, lines.Length);
    }

    [Fact]
    public void HeaderNamesRunPetAndOwner()
    {
        var card = KennelCard.Render(Biscuit(hold: false));
        Assert.StartsWith("RUN 7 — Biscuit (R. Okafor)\n", card);
    }
}
