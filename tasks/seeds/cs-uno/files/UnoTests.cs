namespace Uno.Tests;

public class RulesTests
{
    [Theory]
    [InlineData("R5", "R8", "R", true)]   // active-color match
    [InlineData("G8", "R8", "R", true)]   // same digit across colors
    [InlineData("G8", "R7", "R", false)]
    [InlineData("GS", "RS", "R", true)]   // same action kind across colors
    [InlineData("GD", "RS", "R", false)]  // different action kinds
    [InlineData("W", "R5", "R", true)]    // wild always
    [InlineData("G3", "W", "R", false)]   // wild on top: only the picked color matters
    [InlineData("R3", "W", "R", true)]
    [InlineData("RD", "R5", "R", true)]   // action matching by color
    public void PlayLegalityLaw(string card, string top, string active, bool expected)
    {
        Assert.Equal(expected, Rules.IsPlayable(card, top, active));
    }
}

public class SetupTests
{
    [Fact]
    public void DealsRoundRobinAndFlipsTheStarter()
    {
        var game = Game.Start(Fixtures.Deck("setup.deck"), 3);
        Assert.Equal(new[] { "R0", "R3", "GS", "B7", "YD", "W", "G9" }, game.Hand("P1"));
        Assert.Equal(new[] { "B1", "YR", "G4", "RD", "B5", "Y0", "RS" }, game.Hand("P2"));
        Assert.Equal(new[] { "Y2", "G6", "BS", "R8", "BD", "W", "Y7" }, game.Hand("P3"));
        Assert.Equal("G5", game.TopCard);
        Assert.Equal("G", game.ActiveColor);
        Assert.Equal("P1", game.CurrentPlayer);
        Assert.True(game.DirectionClockwise);
        Assert.Equal(2, game.PileCount);
        Assert.False(game.IsOver);
    }

    [Fact]
    public void HandSizeIsConfigurable()
    {
        var game = Game.Start(Fixtures.Deck("fullgame.deck"), 3, 3);
        Assert.Equal(new[] { "R5", "GD", "W" }, game.Hand("P1"));
        Assert.Equal("R1", game.TopCard);
        Assert.Equal(5, game.PileCount);
    }

    [Fact]
    public void RejectsPlayerCountOutsideTwoToFour()
    {
        Assert.Throws<ArgumentException>(() => Game.Start(Fixtures.Deck("setup.deck"), 1));
        Assert.Throws<ArgumentException>(() => Game.Start(Fixtures.Deck("setup.deck"), 5));
    }

    [Fact]
    public void RejectsHandSizeOutsideOneToTen()
    {
        Assert.Throws<ArgumentException>(() => Game.Start(Fixtures.Deck("setup.deck"), 2, 0));
        Assert.Throws<ArgumentException>(() => Game.Start(Fixtures.Deck("setup.deck"), 2, 11));
    }

    [Fact]
    public void RejectsADeckTooSmallToDealAndFlip()
    {
        Assert.Throws<ArgumentException>(() => Game.Start("R1 G2 B3 Y4", 2, 2));
    }

    [Fact]
    public void RejectsANonNumberFlip()
    {
        Assert.Throws<ArgumentException>(() => Game.Start("R1 G2 B3 Y4 W", 2, 2));
    }

    [Fact]
    public void RejectsMalformedCardTokens()
    {
        Assert.Throws<FormatException>(() => Game.Start("R1 G2 X3 Y4 R5", 2, 2));
        Assert.Throws<FormatException>(() => Game.Start("R1 G2 W4 Y4 R5", 2, 2));
        Assert.Throws<FormatException>(() => Game.Start("R1 G2 B Y4 R5", 2, 2));
    }

    [Fact]
    public void HandLookupRequiresARealPlayer()
    {
        var game = Game.Start(Fixtures.Deck("setup.deck"), 3);
        Assert.Throws<ArgumentException>(() => game.Hand("P4"));
    }
}

public class PlayTests
{
    private static Game Setup() => Game.Start(Fixtures.Deck("setup.deck"), 3);

    [Fact]
    public void ColorMatchAdvancesToTheNextPlayer()
    {
        var game = Setup();
        Assert.Equal(new[] { "P1 plays G9" }, game.Execute("play G9"));
        Assert.Equal("P2", game.CurrentPlayer);
        Assert.Equal("G9", game.TopCard);
        Assert.Equal("G", game.ActiveColor);
        Assert.Equal(new[] { "R0", "R3", "GS", "B7", "YD", "W" }, game.Hand("P1"));
    }

    [Fact]
    public void SkipJumpsTheNextPlayer()
    {
        var game = Setup();
        Assert.Equal(new[] { "P1 plays GS", "P2 is skipped" }, game.Execute("play GS"));
        Assert.Equal("P3", game.CurrentPlayer);
    }

    [Fact]
    public void WildSetsThePickedColor()
    {
        var game = Setup();
        Assert.Equal(new[] { "P1 plays W and picks B" }, game.Execute("play W B"));
        Assert.Equal("B", game.ActiveColor);
        Assert.Equal("W", game.TopCard);
        Assert.Equal("P2", game.CurrentPlayer);
    }

    [Fact]
    public void WildWithoutAColorIsRejected()
    {
        Assert.Throws<InvalidOperationException>(() => Setup().Execute("play W"));
    }

    [Fact]
    public void ColorPickOnANonWildIsRejected()
    {
        Assert.Throws<InvalidOperationException>(() => Setup().Execute("play G9 R"));
    }

    [Fact]
    public void CannotPlayACardYouDoNotHold()
    {
        Assert.Throws<InvalidOperationException>(() => Setup().Execute("play B1"));
    }

    [Fact]
    public void CannotPlayAnUnmatchedCard()
    {
        Assert.Throws<InvalidOperationException>(() => Setup().Execute("play B7"));
    }

    [Fact]
    public void ReverseFlipsTheTurnOrderWithThreePlayers()
    {
        var game = Game.Start(Fixtures.Deck("reverse3.deck"), 3, 2);
        Assert.Equal(new[] { "P1 plays GR", "direction is now counter-clockwise" },
                     game.Execute("play GR"));
        Assert.False(game.DirectionClockwise);
        Assert.Equal("P3", game.CurrentPlayer);
        game.Execute("play G8");
        Assert.Equal("P2", game.CurrentPlayer);
    }

    [Fact]
    public void ReverseHeadsUpActsAsASkip()
    {
        var game = Game.Start(Fixtures.Deck("reverse2.deck"), 2, 2);
        Assert.Equal(new[] { "P1 plays GR", "direction is now counter-clockwise", "P2 is skipped" },
                     game.Execute("play GR"));
        Assert.Equal("P1", game.CurrentPlayer);
        Assert.False(game.DirectionClockwise);
    }

    [Fact]
    public void DrawTwoVictimDrawsAndLosesTheTurn()
    {
        var game = Game.Start(Fixtures.Deck("drawtwo.deck"), 2, 2);
        Assert.Equal(new[] { "P1 plays RD", "P2 draws 2 and is skipped" },
                     game.Execute("play RD"));
        Assert.Equal(new[] { "B4", "Y6", "G7", "B9" }, game.Hand("P2"));
        Assert.Equal("P1", game.CurrentPlayer);
        Assert.Equal(0, game.PileCount);
    }

    [Fact]
    public void MalformedCommandsAreRejected()
    {
        Assert.Throws<FormatException>(() => Setup().Execute("shuffle"));
        Assert.Throws<FormatException>(() => Setup().Execute("play"));
        Assert.Throws<FormatException>(() => Setup().Execute("play Q5"));
    }
}

public class DrawTests
{
    private static Game ForcedDraw() => Game.Start(Fixtures.Deck("forceddraw.deck"), 2, 2);

    [Fact]
    public void DrawnWildIsForcePlayedWithTheScriptedColor()
    {
        var game = ForcedDraw();
        Assert.Equal(new[] { "P1 draws W and plays it and picks R" }, game.Execute("draw R"));
        Assert.Equal("R", game.ActiveColor);
        Assert.Equal("W", game.TopCard);
        Assert.Equal("P2", game.CurrentPlayer);
        Assert.Equal(new[] { "B4", "Y6" }, game.Hand("P1")); // played straight from the pile
    }

    [Fact]
    public void DrawnWildWithoutAColorIsRejectedAndNothingIsConsumed()
    {
        var game = ForcedDraw();
        Assert.Throws<InvalidOperationException>(() => game.Execute("draw"));
        Assert.Equal(3, game.PileCount);
        Assert.Equal(new[] { "P1 draws W and plays it and picks R" }, game.Execute("draw R"));
    }

    [Fact]
    public void UnplayableDrawIsKeptAndTheTurnPasses()
    {
        var game = ForcedDraw();
        game.Execute("draw R");
        Assert.Equal(new[] { "P2 draws B2 and keeps it" }, game.Execute("draw"));
        Assert.Equal(new[] { "G3", "Y8", "B2" }, game.Hand("P2"));
        Assert.Equal("P1", game.CurrentPlayer);
    }

    [Fact]
    public void PlayableDrawnNumberIsForcePlayed()
    {
        var game = ForcedDraw();
        game.Execute("draw R");
        game.Execute("draw");
        Assert.Equal(new[] { "P1 draws R9 and plays it" }, game.Execute("draw"));
        Assert.Equal("R9", game.TopCard);
        Assert.Equal("P2", game.CurrentPlayer);
    }

    [Fact]
    public void ColorPickIsRejectedWhenTheDrawnCardIsNotAWild()
    {
        var game = ForcedDraw();
        game.Execute("draw R");
        game.Execute("draw");
        Assert.Throws<InvalidOperationException>(() => game.Execute("draw G"));
        Assert.Equal(1, game.PileCount);
    }

    [Fact]
    public void DrawingFromAnEmptyPileIsRejected()
    {
        var game = Game.Start("B4 G3 Y6 Y8 G5", 2, 2);
        Assert.Throws<InvalidOperationException>(() => game.Execute("draw"));
    }
}

public class FullGameTests
{
    [Fact]
    public void ScriptedThreeHanderMatchesThePinnedTranscript()
    {
        var game = Game.Start(Fixtures.Deck("fullgame.deck"), 3, 3);
        var expected = string.Join('\n',
            "P1 plays R5",
            "P2 plays R7",
            "P3 draws G7 and plays it",
            "P1 plays GD",
            "P2 draws 2 and is skipped",
            "P3 plays G2",
            "P1 plays W and picks B",
            "P1 wins");
        Assert.Equal(expected, game.RunScript(Fixtures.Deck("fullgame.script")));
    }

    [Fact]
    public void FinishedGameFreezesAndReportsTheWinner()
    {
        var game = Game.Start(Fixtures.Deck("fullgame.deck"), 3, 3);
        game.RunScript(Fixtures.Deck("fullgame.script"));
        Assert.True(game.IsOver);
        Assert.Equal("P1", game.Winner);
        Assert.Empty(game.Hand("P1"));
        Assert.Equal(new[] { "RS", "B3", "B2", "Y4" }, game.Hand("P2"));
        Assert.Equal(2, game.PileCount);
        Assert.Throws<InvalidOperationException>(() => game.Execute("draw"));
    }

    [Fact]
    public void WinnerIsNullWhileTheGameIsLive()
    {
        var game = Game.Start(Fixtures.Deck("fullgame.deck"), 3, 3);
        Assert.Null(game.Winner);
    }
}

internal static class Fixtures
{
    public static string Deck(string name) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "testdata", name));
}
