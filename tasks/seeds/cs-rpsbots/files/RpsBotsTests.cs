namespace RpsBots.Tests;

public class RulesTests
{
    [Theory]
    [InlineData(Move.Rock, Move.Scissors, true)]
    [InlineData(Move.Scissors, Move.Paper, true)]
    [InlineData(Move.Paper, Move.Rock, true)]
    [InlineData(Move.Scissors, Move.Rock, false)]
    [InlineData(Move.Paper, Move.Scissors, false)]
    [InlineData(Move.Rock, Move.Paper, false)]
    [InlineData(Move.Rock, Move.Rock, false)]
    [InlineData(Move.Paper, Move.Paper, false)]
    [InlineData(Move.Scissors, Move.Scissors, false)]
    public void BeatsTruthTable(Move a, Move b, bool expected)
    {
        Assert.Equal(expected, Rules.Beats(a, b));
    }

    [Theory]
    [InlineData(Move.Rock, Move.Paper)]
    [InlineData(Move.Paper, Move.Scissors)]
    [InlineData(Move.Scissors, Move.Rock)]
    public void CounterBeatsItsTarget(Move target, Move counter)
    {
        Assert.Equal(counter, Rules.Counter(target));
    }
}

public class BotTests
{
    private static readonly Move[] None = Array.Empty<Move>();

    [Fact]
    public void ConstantBotNeverWavers()
    {
        var bot = new ConstantBot("Rocky", Move.Rock);
        Assert.Equal(Move.Rock, bot.Next(None));
        Assert.Equal(Move.Rock, bot.Next(new[] { Move.Paper, Move.Paper }));
    }

    [Fact]
    public void CyclerBotAdvancesByRoundsPlayed()
    {
        var bot = new CyclerBot("Cycla", new[] { Move.Rock, Move.Paper, Move.Scissors });
        Assert.Equal(Move.Rock, bot.Next(None));
        Assert.Equal(Move.Paper, bot.Next(new[] { Move.Rock }));
        Assert.Equal(Move.Scissors, bot.Next(new[] { Move.Rock, Move.Rock }));
        Assert.Equal(Move.Rock, bot.Next(new[] { Move.Rock, Move.Rock, Move.Rock }));
    }

    [Fact]
    public void CyclerBotRejectsEmptySequence()
    {
        Assert.Throws<ArgumentException>(() => new CyclerBot("Cycla", Array.Empty<Move>()));
    }

    [Fact]
    public void CopyLastBotOpensWithRockThenMirrors()
    {
        var bot = new CopyLastBot("Echo");
        Assert.Equal(Move.Rock, bot.Next(None));
        Assert.Equal(Move.Scissors, bot.Next(new[] { Move.Paper, Move.Scissors }));
    }

    [Fact]
    public void FrequencyBotOpensWithPaper()
    {
        // no history: the all-zero tie resolves to Rock, so the bot counters with Paper
        Assert.Equal(Move.Paper, new FrequencyBot("Tally").Next(None));
    }

    [Fact]
    public void FrequencyBotCountersTheMostFrequentMove()
    {
        var bot = new FrequencyBot("Tally");
        Assert.Equal(Move.Rock, bot.Next(new[] { Move.Scissors, Move.Scissors, Move.Paper }));
    }

    [Theory]
    [InlineData(new[] { Move.Rock, Move.Scissors }, Move.Paper)]          // R/S tie -> Rock first
    [InlineData(new[] { Move.Paper, Move.Scissors, Move.Scissors, Move.Paper }, Move.Scissors)] // P/S tie -> Paper first
    public void FrequencyBotBreaksTiesInRockPaperScissorsOrder(Move[] history, Move expected)
    {
        Assert.Equal(expected, new FrequencyBot("Tally").Next(history));
    }
}

public class MatchTests
{
    [Fact]
    public void PlaysTheRequestedNumberOfRounds()
    {
        var result = Match.Play(new ConstantBot("Rocky", Move.Rock),
                                new ConstantBot("Papyr", Move.Paper), 3);
        Assert.Equal(0, result.WinsA);
        Assert.Equal(3, result.WinsB);
        Assert.Equal(0, result.Draws);
        Assert.Equal(new[]
        {
            "R1: Rocky ROCK vs Papyr PAPER -> Papyr",
            "R2: Rocky ROCK vs Papyr PAPER -> Papyr",
            "R3: Rocky ROCK vs Papyr PAPER -> Papyr",
        }, result.Transcript);
    }

    [Fact]
    public void DrawnRoundsSayDraw()
    {
        var result = Match.Play(new ConstantBot("A", Move.Scissors),
                                new ConstantBot("B", Move.Scissors), 1);
        Assert.Equal(1, result.Draws);
        Assert.Equal("R1: A SCISSORS vs B SCISSORS -> draw", result.Transcript[0]);
    }

    [Fact]
    public void EachBotSeesOnlyTheOpponentHistory()
    {
        var result = Match.Play(new CopyLastBot("Echo"),
                                new CyclerBot("Cycla", new[] { Move.Paper, Move.Scissors }), 3);
        Assert.Equal(new[]
        {
            "R1: Echo ROCK vs Cycla PAPER -> Cycla",
            "R2: Echo PAPER vs Cycla SCISSORS -> Cycla",
            "R3: Echo SCISSORS vs Cycla PAPER -> Echo",
        }, result.Transcript);
    }

    [Fact]
    public void RejectsNonPositiveRoundCount()
    {
        Assert.Throws<ArgumentOutOfRangeException>(
            () => Match.Play(new CopyLastBot("A"), new CopyLastBot("B"), 0));
    }
}

public class RosterTests
{
    [Fact]
    public void RejectsUnknownBotKind()
    {
        Assert.Throws<FormatException>(() => Tournament.Run("Zed mystery\n", 3));
    }

    [Fact]
    public void RejectsBadMoveLetter()
    {
        Assert.Throws<FormatException>(() => Tournament.Run("Ann constant X\nBob copylast\n", 3));
        Assert.Throws<FormatException>(() => Tournament.Run("Ann cycler RQ\nBob copylast\n", 3));
    }

    [Fact]
    public void RejectsDuplicateNames()
    {
        Assert.Throws<ArgumentException>(
            () => Tournament.Run("Ann constant R\nAnn copylast\n", 3));
    }

    [Fact]
    public void RejectsRostersSmallerThanTwo()
    {
        Assert.Throws<ArgumentException>(() => Tournament.Run("# only comments\nAnn counter\n", 3));
    }
}

public class TournamentTests
{
    [Fact]
    public void NameBreaksTheFinalTie()
    {
        var report = Tournament.Run("Beta constant R\nAlpha constant R\n", 3);
        var lines = report.Split('\n');
        Assert.Equal("1. Alpha pts=1 w=0 d=1 l=0 rw=0 rl=0", lines[^2]);
        Assert.Equal("2. Beta pts=1 w=0 d=1 l=0 rw=0 rl=0", lines[^1]);
    }

    [Fact]
    public void RunsTheFixtureLeagueExactly()
    {
        var roster = File.ReadAllText(
            Path.Combine(AppContext.BaseDirectory, "testdata", "roster.txt"));
        var expected = string.Join('\n',
            "MATCH Rocky vs Cycla",
            "R1: Rocky ROCK vs Cycla ROCK -> draw",
            "R2: Rocky ROCK vs Cycla PAPER -> Cycla",
            "R3: Rocky ROCK vs Cycla SCISSORS -> Rocky",
            "R4: Rocky ROCK vs Cycla ROCK -> draw",
            "R5: Rocky ROCK vs Cycla PAPER -> Cycla",
            "RESULT Rocky 1 Cycla 2",
            "MATCH Rocky vs Echo",
            "R1: Rocky ROCK vs Echo ROCK -> draw",
            "R2: Rocky ROCK vs Echo ROCK -> draw",
            "R3: Rocky ROCK vs Echo ROCK -> draw",
            "R4: Rocky ROCK vs Echo ROCK -> draw",
            "R5: Rocky ROCK vs Echo ROCK -> draw",
            "RESULT Rocky 0 Echo 0",
            "MATCH Rocky vs Tally",
            "R1: Rocky ROCK vs Tally PAPER -> Tally",
            "R2: Rocky ROCK vs Tally PAPER -> Tally",
            "R3: Rocky ROCK vs Tally PAPER -> Tally",
            "R4: Rocky ROCK vs Tally PAPER -> Tally",
            "R5: Rocky ROCK vs Tally PAPER -> Tally",
            "RESULT Rocky 0 Tally 5",
            "MATCH Cycla vs Echo",
            "R1: Cycla ROCK vs Echo ROCK -> draw",
            "R2: Cycla PAPER vs Echo ROCK -> Cycla",
            "R3: Cycla SCISSORS vs Echo PAPER -> Cycla",
            "R4: Cycla ROCK vs Echo SCISSORS -> Cycla",
            "R5: Cycla PAPER vs Echo ROCK -> Cycla",
            "RESULT Cycla 4 Echo 0",
            "MATCH Cycla vs Tally",
            "R1: Cycla ROCK vs Tally PAPER -> Tally",
            "R2: Cycla PAPER vs Tally PAPER -> draw",
            "R3: Cycla SCISSORS vs Tally PAPER -> Cycla",
            "R4: Cycla ROCK vs Tally PAPER -> Tally",
            "R5: Cycla PAPER vs Tally PAPER -> draw",
            "RESULT Cycla 1 Tally 2",
            "MATCH Echo vs Tally",
            "R1: Echo ROCK vs Tally PAPER -> Tally",
            "R2: Echo PAPER vs Tally PAPER -> draw",
            "R3: Echo PAPER vs Tally PAPER -> draw",
            "R4: Echo PAPER vs Tally SCISSORS -> Tally",
            "R5: Echo SCISSORS vs Tally SCISSORS -> draw",
            "RESULT Echo 0 Tally 2",
            "STANDINGS",
            "1. Tally pts=9 w=3 d=0 l=0 rw=9 rl=1",
            "2. Cycla pts=6 w=2 d=0 l=1 rw=7 rl=3",
            "3. Rocky pts=1 w=0 d=1 l=2 rw=1 rl=7",
            "4. Echo pts=1 w=0 d=1 l=2 rw=0 rl=6");
        Assert.Equal(expected, Tournament.Run(roster, 5));
    }

    [Fact]
    public void RoundWinsBreakTheTieWhenDifferenceIsEqual()
    {
        // Rocky and Echo tie on points (1) and on rw-rl (-6); Rocky's 1 round win ranks him above
        var roster = File.ReadAllText(
            Path.Combine(AppContext.BaseDirectory, "testdata", "roster.txt"));
        var lines = Tournament.Run(roster, 5).Split('\n');
        Assert.Equal("3. Rocky pts=1 w=0 d=1 l=2 rw=1 rl=7", lines[^2]);
        Assert.Equal("4. Echo pts=1 w=0 d=1 l=2 rw=0 rl=6", lines[^1]);
    }
}
