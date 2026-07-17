namespace Tenpin.Tests;

public class ParseTests
{
    [Fact]
    public void ParsesWhitespaceAndComments()
    {
        var game = Game.Parse("# warmup\n1 4\n\n  4 5\n6 4 # spare\n");
        Assert.Equal(new int?[] { 5, 14, null, null, null, null, null, null, null, null },
                     game.FrameScores());
    }

    [Fact]
    public void RejectsNonNumericToken()
    {
        Assert.Throws<FormatException>(() => Game.Parse("1 4 ten"));
    }

    [Fact]
    public void RejectsOutOfRangeToken()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => Game.Parse("1 4 11"));
    }
}

public class RollValidationTests
{
    [Fact]
    public void RejectsNegativePinfall()
    {
        var game = new Game();
        Assert.Throws<ArgumentOutOfRangeException>(() => game.Roll(-1));
    }

    [Fact]
    public void RejectsPinfallAboveTen()
    {
        var game = new Game();
        Assert.Throws<ArgumentOutOfRangeException>(() => game.Roll(11));
    }

    [Fact]
    public void RejectsFrameSumAboveTen()
    {
        var game = new Game();
        game.Roll(6);
        Assert.Throws<ArgumentException>(() => game.Roll(7));
    }

    [Fact]
    public void SecondBallAfterStrikeStartsNextFrame()
    {
        var game = new Game();
        game.Roll(10);
        game.Roll(9); // frame 2, ball 1 — must be accepted
        game.Roll(1);
        Assert.Equal(20, game.FrameScores()[0]);
    }

    [Fact]
    public void TenthFrameOpenGetsNoThirdBall()
    {
        var game = OpenFrames(9);
        game.Roll(4);
        game.Roll(3);
        Assert.True(game.IsComplete);
        Assert.Throws<InvalidOperationException>(() => game.Roll(1));
    }

    [Fact]
    public void TenthFrameSpareGetsExactlyOneFillBall()
    {
        var game = OpenFrames(9);
        game.Roll(4);
        game.Roll(6);
        Assert.False(game.IsComplete);
        game.Roll(10); // fill ball may be a strike
        Assert.True(game.IsComplete);
        Assert.Throws<InvalidOperationException>(() => game.Roll(1));
    }

    [Fact]
    public void TenthFrameStrikeThenNonStrikePairIsRackLimited()
    {
        var game = OpenFrames(9);
        game.Roll(10);
        game.Roll(5);
        Assert.Throws<ArgumentException>(() => game.Roll(7));
    }

    [Fact]
    public void TenthFrameStrikeThenSpareIsLegal()
    {
        var game = OpenFrames(9);
        game.Roll(10);
        game.Roll(9);
        game.Roll(1);
        Assert.True(game.IsComplete);
        Assert.Equal(20, game.Score());
    }

    internal static Game OpenFrames(int count)
    {
        var game = new Game();
        for (var i = 0; i < count; i++) { game.Roll(0); game.Roll(0); }
        return game;
    }
}

public class ScoringTests
{
    [Fact]
    public void ScoreThrowsUntilGameIsComplete()
    {
        var game = new Game();
        game.Roll(10);
        Assert.Throws<InvalidOperationException>(() => game.Score());
    }

    [Fact]
    public void FreshGameHasNoResolvedFrames()
    {
        var game = new Game();
        Assert.False(game.IsComplete);
        Assert.All(game.FrameScores(), s => Assert.Null(s));
    }

    [Fact]
    public void StrikeStaysUnresolvedUntilTwoMoreBalls()
    {
        var game = new Game();
        game.Roll(10);
        game.Roll(4);
        Assert.Null(game.FrameScores()[0]);
        game.Roll(2);
        Assert.Equal(16, game.FrameScores()[0]);
        Assert.Equal(22, game.FrameScores()[1]);
        Assert.Null(game.FrameScores()[2]);
    }

    [Fact]
    public void SpareStaysUnresolvedUntilOneMoreBall()
    {
        var game = new Game();
        game.Roll(5);
        game.Roll(5);
        Assert.Null(game.FrameScores()[0]);
        game.Roll(7);
        Assert.Equal(17, game.FrameScores()[0]);
        Assert.Null(game.FrameScores()[1]);
    }

    [Fact]
    public void PerfectGameScoresThreeHundred()
    {
        var game = Game.Parse(Fixtures.Read("perfect.rolls"));
        Assert.True(game.IsComplete);
        Assert.Equal(300, game.Score());
        Assert.Equal(new int?[] { 30, 60, 90, 120, 150, 180, 210, 240, 270, 300 },
                     game.FrameScores());
    }

    [Fact]
    public void AllSparesWithFiveFillScoresOneFifty()
    {
        var game = Game.Parse(Fixtures.Read("allspares.rolls"));
        Assert.Equal(150, game.Score());
        Assert.Equal(new int?[] { 15, 30, 45, 60, 75, 90, 105, 120, 135, 150 },
                     game.FrameScores());
    }

    [Fact]
    public void MixedGameScoresByTheBook()
    {
        var game = Game.Parse(Fixtures.Read("mixed.rolls"));
        Assert.Equal(133, game.Score());
        Assert.Equal(new int?[] { 5, 14, 29, 49, 60, 61, 77, 97, 117, 133 },
                     game.FrameScores());
    }

    [Fact]
    public void GutterGameScoresZero()
    {
        var game = Game.Parse(Fixtures.Read("gutter.rolls"));
        Assert.Equal(0, game.Score());
    }
}

public class ScoreboardTests
{
    [Fact]
    public void FreshGameRendersEmptyFrames()
    {
        var lines = new Game().Scoreboard().Split('\n');
        Assert.Equal(10, lines.Length);
        Assert.Equal("F1 [] ?", lines[0]);
        Assert.Equal("F10 [] ?", lines[9]);
    }

    [Fact]
    public void MarksUseStrikeSpareAndGutterSymbols()
    {
        var game = new Game();
        game.Roll(10);
        game.Roll(0);
        game.Roll(10);
        game.Roll(0);
        game.Roll(1);
        var lines = game.Scoreboard().Split('\n');
        Assert.Equal("F1 [X] 20", lines[0]);
        Assert.Equal("F2 [- /] 30", lines[1]);   // spare resolved by the next ball
        Assert.Equal("F3 [- 1] 31", lines[2]);
        Assert.Equal("F4 [] ?", lines[3]);
    }

    [Fact]
    public void InProgressFrameShowsBallSoFar()
    {
        var game = new Game();
        game.Roll(10);
        game.Roll(4);
        var lines = game.Scoreboard().Split('\n');
        Assert.Equal("F1 [X] ?", lines[0]);
        Assert.Equal("F2 [4] ?", lines[1]);
    }

    [Fact]
    public void TenthFrameStrikeRunRendersThreeMarks()
    {
        var game = Game.Parse(Fixtures.Read("perfect.rolls"));
        var lines = game.Scoreboard().Split('\n');
        Assert.Equal("F9 [X] 270", lines[8]);
        Assert.Equal("F10 [X X X] 300", lines[9]);
    }

    [Fact]
    public void TenthFrameSpareThenFillRenders()
    {
        var game = RollValidationTests.OpenFrames(9);
        game.Roll(2);
        game.Roll(8);
        game.Roll(6);
        Assert.Equal("F10 [2 / 6] 16", game.Scoreboard().Split('\n')[9]);
    }

    [Fact]
    public void TenthFrameStrikeThenRackedPairRenders()
    {
        var game = RollValidationTests.OpenFrames(9);
        game.Roll(10);
        game.Roll(9);
        game.Roll(1);
        Assert.Equal("F10 [X 9 /] 20", game.Scoreboard().Split('\n')[9]);
    }
}

public class GameRunnerTests
{
    [Fact]
    public void RunsPerfectGameSheet()
    {
        var expected = string.Join('\n',
            "F1 [X] 30", "F2 [X] 60", "F3 [X] 90", "F4 [X] 120", "F5 [X] 150",
            "F6 [X] 180", "F7 [X] 210", "F8 [X] 240", "F9 [X] 270",
            "F10 [X X X] 300", "TOTAL 300");
        Assert.Equal(expected, GameRunner.Run(Fixtures.Read("perfect.rolls")));
    }

    [Fact]
    public void RunsAllSparesSheet()
    {
        var expected = string.Join('\n',
            "F1 [5 /] 15", "F2 [5 /] 30", "F3 [5 /] 45", "F4 [5 /] 60",
            "F5 [5 /] 75", "F6 [5 /] 90", "F7 [5 /] 105", "F8 [5 /] 120",
            "F9 [5 /] 135", "F10 [5 / 5] 150", "TOTAL 150");
        Assert.Equal(expected, GameRunner.Run(Fixtures.Read("allspares.rolls")));
    }

    [Fact]
    public void RunsMixedSheet()
    {
        var expected = string.Join('\n',
            "F1 [1 4] 5", "F2 [4 5] 14", "F3 [6 /] 29", "F4 [5 /] 49",
            "F5 [X] 60", "F6 [- 1] 61", "F7 [7 /] 77", "F8 [6 /] 97",
            "F9 [X] 117", "F10 [2 / 6] 133", "TOTAL 133");
        Assert.Equal(expected, GameRunner.Run(Fixtures.Read("mixed.rolls")));
    }

    [Fact]
    public void RunsGutterSheet()
    {
        var lines = GameRunner.Run(Fixtures.Read("gutter.rolls")).Split('\n');
        Assert.Equal("F1 [- -] 0", lines[0]);
        Assert.Equal("F10 [- -] 0", lines[9]);
        Assert.Equal("TOTAL 0", lines[10]);
    }

    [Fact]
    public void RejectsIncompleteSheet()
    {
        Assert.Throws<InvalidOperationException>(() => GameRunner.Run("10 10"));
    }
}

internal static class Fixtures
{
    public static string Read(string name) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "testdata", name));
}
