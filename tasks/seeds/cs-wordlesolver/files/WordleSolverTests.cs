namespace WordleSolver.Tests;

public class FeedbackTests
{
    [Fact]
    public void ExactMatchIsAllGreen()
    {
        Assert.Equal("GGGGG", Feedbacks.Score("crane", "crane"));
    }

    [Theory]
    [InlineData("speed", "abide", "BBYBY")]
    [InlineData("erase", "speed", "YBBYY")]
    [InlineData("llama", "label", "GYYBB")]
    [InlineData("added", "dread", "YYBYG")]
    public void DuplicateLettersFollowGreensFirstThenLeftToRight(
        string guess, string answer, string expected)
    {
        Assert.Equal(expected, Feedbacks.Score(guess, answer));
    }

    [Fact]
    public void YellowIsNotRepeatedOnceTheAnswerLetterIsSpent()
    {
        // answer has a single 'o'; only the first stray 'o' in the guess earns Y
        Assert.Equal("BYYBB", Feedbacks.Score("crook", "board"));
    }

    [Fact]
    public void RejectsWrongLengthWords()
    {
        Assert.Throws<ArgumentException>(() => Feedbacks.Score("four", "abide"));
        Assert.Throws<ArgumentException>(() => Feedbacks.Score("abide", "sixers"));
    }

    [Fact]
    public void RejectsNonLowercaseLetters()
    {
        Assert.Throws<ArgumentException>(() => Feedbacks.Score("SPEED", "abide"));
        Assert.Throws<ArgumentException>(() => Feedbacks.Score("spee1", "abide"));
    }
}

public class CandidateListTests
{
    [Fact]
    public void LoadsFixtureListSortedAscending()
    {
        var solver = new Solver(Fixtures.Candidates(), "crane");
        Assert.Equal(206, solver.Remaining);
        Assert.Equal("abide", solver.Candidates[0]);
        Assert.Equal("cycle", solver.Candidates[205]);
    }

    [Fact]
    public void SkipsCommentsAndBlankLinesAndSortsInput()
    {
        var solver = new Solver("delta\nebony\n# note to self\n\ncrust\n", "crust");
        Assert.Equal(new[] { "crust", "delta", "ebony" }, solver.Candidates);
    }

    [Fact]
    public void RejectsDuplicateWords()
    {
        Assert.Throws<ArgumentException>(() => new Solver("crust\ndelta\ncrust\n", "crust"));
    }

    [Fact]
    public void RejectsMalformedWords()
    {
        Assert.Throws<FormatException>(() => new Solver("crust\nfour\n", "crust"));
        Assert.Throws<FormatException>(() => new Solver("crust\nAbide\n", "crust"));
    }

    [Fact]
    public void RejectsMalformedFirstGuess()
    {
        Assert.Throws<ArgumentException>(() => new Solver("crust\n", "no"));
    }
}

public class FilteringTests
{
    [Fact]
    public void FirstGuessIsThePinnedOpener()
    {
        Assert.Equal("crane", Fixtures.Solver().NextGuess());
    }

    [Fact]
    public void ObserveNarrowsToConsistentWordsOnly()
    {
        var solver = Fixtures.Solver();
        solver.Observe("crane", "GBGBB");
        Assert.Equal(8, solver.Remaining);
        Assert.Equal(new[] { "chalk", "claim", "clamp", "clash", "clasp", "class", "coach", "coast" },
                     solver.Candidates);
        Assert.Equal("chalk", solver.NextGuess());
    }

    [Fact]
    public void ChainedObservationsKeepNarrowing()
    {
        var solver = Fixtures.Solver();
        solver.Observe("crane", "GBGBB");
        solver.Observe("chalk", "GBGYB");
        Assert.Equal(new[] { "claim", "clamp", "clasp", "class" }, solver.Candidates);
        Assert.Equal("claim", solver.NextGuess());
        solver.Observe("claim", "GGGBB");
        Assert.Equal(new[] { "clasp", "class" }, solver.Candidates);
    }

    [Fact]
    public void AllBlackFeedbackPrunesEveryWordSharingLetters()
    {
        var solver = Fixtures.Solver();
        solver.Observe("crane", "BBBBB");
        Assert.Equal(8, solver.Remaining);
        Assert.All(solver.Candidates, w => Assert.Empty(w.Intersect("crane")));
    }

    [Fact]
    public void ContradictoryFeedbackEmptiesThePool()
    {
        var solver = Fixtures.Solver();
        solver.Observe("bliss", "GGGGG");
        Assert.Equal(1, solver.Remaining);
        solver.Observe("bliss", "BBBBB");
        Assert.Equal(0, solver.Remaining);
        Assert.Throws<InvalidOperationException>(() => solver.NextGuess());
    }

    [Fact]
    public void RejectsMalformedFeedback()
    {
        var solver = Fixtures.Solver();
        Assert.Throws<ArgumentException>(() => solver.Observe("crane", "GYBXZ"));
        Assert.Throws<ArgumentException>(() => solver.Observe("crane", "GYBB"));
    }
}

public class SolveTests
{
    [Fact]
    public void SolvesTheOpenerInOneGuess()
    {
        Assert.Equal(new[] { "crane" }, Fixtures.Solver().Solve("crane"));
    }

    [Fact]
    public void SolvesCountInThree()
    {
        Assert.Equal(new[] { "crane", "cling", "count" }, Fixtures.Solver().Solve("count"));
    }

    [Fact]
    public void SolvesClimbInFive()
    {
        Assert.Equal(new[] { "crane", "child", "click", "cliff", "climb" },
                     Fixtures.Solver().Solve("climb"));
    }

    [Fact]
    public void SolvesAppleInFive()
    {
        Assert.Equal(new[] { "crane", "abide", "algae", "ample", "apple" },
                     Fixtures.Solver().Solve("apple"));
    }

    [Fact]
    public void SolveDoesNotDisturbInteractiveState()
    {
        var solver = Fixtures.Solver();
        solver.Observe("crane", "GBGBB");
        solver.Solve("climb");
        Assert.Equal(8, solver.Remaining);
        Assert.Equal("chalk", solver.NextGuess());
    }

    [Fact]
    public void SolveRejectsAnswersOutsideTheCandidateList()
    {
        Assert.Throws<ArgumentException>(() => Fixtures.Solver().Solve("zesty"));
    }

    [Fact]
    public void SolveThrowsWhenGuessBudgetRunsOut()
    {
        Assert.Throws<InvalidOperationException>(() => Fixtures.Solver().Solve("climb", 3));
    }
}

internal static class Fixtures
{
    public static string Candidates() =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "testdata", "candidates.txt"));

    public static Solver Solver() => new(Candidates(), "crane");
}
