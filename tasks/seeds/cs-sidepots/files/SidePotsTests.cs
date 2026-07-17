namespace SidePots.Tests;

public class TableSetupTests
{
    [Fact]
    public void RejectsFewerThanTwoPlayers()
    {
        Assert.Throws<ArgumentException>(() => new Table(new[] { ("Ann", 100) }, 0));
    }

    [Fact]
    public void RejectsDuplicateNames()
    {
        Assert.Throws<ArgumentException>(
            () => new Table(new[] { ("Ann", 100), ("Ann", 60) }, 0));
    }

    [Fact]
    public void RejectsNonPositiveStacks()
    {
        Assert.Throws<ArgumentException>(
            () => new Table(new[] { ("Ann", 100), ("Bob", 0) }, 0));
    }

    [Fact]
    public void RejectsButtonOutsideSeatRange()
    {
        Assert.Throws<ArgumentException>(
            () => new Table(new[] { ("Ann", 100), ("Bob", 60) }, 2));
    }
}

public class LedgerTests
{
    private static Table HeadsUp() => new(new[] { ("Ann", 100), ("Bob", 20) }, 0);

    [Fact]
    public void RaiseSetsTotalCommitmentAndCurrentBet()
    {
        var table = HeadsUp();
        table.Apply("Ann raise 50");
        Assert.Equal(50, table.Committed("Ann"));
        Assert.Equal(50, table.CurrentBet);
        Assert.False(table.IsAllIn("Ann"));
    }

    [Fact]
    public void CallForLessGoesAllIn()
    {
        var table = HeadsUp();
        table.Apply("Ann raise 50\nBob call");
        Assert.Equal(20, table.Committed("Bob"));
        Assert.True(table.IsAllIn("Bob"));
        Assert.Equal(50, table.CurrentBet);
    }

    [Fact]
    public void RaiseToFullStackIsAllIn()
    {
        var table = HeadsUp();
        table.Apply("Ann raise 100");
        Assert.True(table.IsAllIn("Ann"));
    }

    [Fact]
    public void CheckIsOnlyLegalWhenNotBehind()
    {
        var table = HeadsUp();
        table.Apply("Ann check\nBob check");
        Assert.Equal(0, table.CurrentBet);
        table.Apply("Ann raise 10");
        Assert.Throws<InvalidOperationException>(() => table.Apply("Bob check"));
    }

    [Fact]
    public void FoldedPlayersCannotAct()
    {
        var table = HeadsUp();
        table.Apply("Ann raise 10\nBob fold");
        Assert.True(table.IsFolded("Bob"));
        Assert.Throws<InvalidOperationException>(() => table.Apply("Bob call"));
    }

    [Fact]
    public void AllInPlayersCannotAct()
    {
        var table = HeadsUp();
        table.Apply("Ann raise 50\nBob call");
        Assert.Throws<InvalidOperationException>(() => table.Apply("Bob fold"));
    }

    [Fact]
    public void RaiseMustExceedCurrentBet()
    {
        var table = HeadsUp();
        table.Apply("Ann raise 50");
        Assert.Throws<InvalidOperationException>(() => table.Apply("Bob raise 50"));
    }

    [Fact]
    public void RaiseCannotExceedStack()
    {
        var table = HeadsUp();
        Assert.Throws<InvalidOperationException>(() => table.Apply("Bob raise 21"));
    }

    [Fact]
    public void TheLeadingBetCannotFold()
    {
        var table = HeadsUp();
        table.Apply("Ann raise 40");
        Assert.Throws<InvalidOperationException>(() => table.Apply("Ann fold"));
    }

    [Fact]
    public void UnknownPlayerIsRejected()
    {
        Assert.Throws<ArgumentException>(() => HeadsUp().Apply("Zed call"));
    }

    [Fact]
    public void MalformedActionIsRejected()
    {
        Assert.Throws<FormatException>(() => HeadsUp().Apply("Ann wager 5"));
        Assert.Throws<FormatException>(() => HeadsUp().Apply("Ann raise ten"));
    }

    [Fact]
    public void CommentsAndBlankLinesAreIgnored()
    {
        var table = HeadsUp();
        table.Apply("# preflop\n\nAnn raise 10  # opens\nBob call\n");
        Assert.Equal(10, table.Committed("Bob"));
    }
}

public class PotConstructionTests
{
    [Fact]
    public void ThreeWayAllInBuildsLayeredPotsAndRefund()
    {
        var table = new Table(new[] { ("Ann", 100), ("Bob", 60), ("Cid", 30) }, 0);
        table.Apply(Fixtures.Hand("threeway.hand"));
        var report = table.BuildPots();
        Assert.Equal(2, report.Pots.Count);
        Assert.Equal(90, report.Pots[0].Amount);
        Assert.Equal(new[] { "Ann", "Bob", "Cid" }, report.Pots[0].Eligible);
        Assert.Equal(60, report.Pots[1].Amount);
        Assert.Equal(new[] { "Ann", "Bob" }, report.Pots[1].Eligible);
        Assert.Equal(40, report.Refunds["Ann"]);
    }

    [Fact]
    public void FoldedChipsStayInThePot()
    {
        var table = new Table(new[] { ("Ann", 200), ("Bob", 200), ("Cid", 200) }, 2);
        table.Apply("Ann raise 40\nBob call\nCid raise 100\nAnn call\nBob fold");
        var report = table.BuildPots();
        var pot = Assert.Single(report.Pots);
        Assert.Equal(240, pot.Amount);
        Assert.Equal(new[] { "Ann", "Cid" }, pot.Eligible);
        Assert.Empty(report.Refunds);
    }

    [Fact]
    public void ShortStackAllInSplitsTheLayers()
    {
        var table = new Table(new[] { ("Ann", 80), ("Bob", 80), ("Cid", 25), ("Dee", 80) }, 3);
        table.Apply(Fixtures.Hand("shortstack.hand"));
        var report = table.BuildPots();
        Assert.Equal(2, report.Pots.Count);
        Assert.Equal(100, report.Pots[0].Amount);
        Assert.Equal(new[] { "Ann", "Bob", "Cid", "Dee" }, report.Pots[0].Eligible);
        Assert.Equal(15, report.Pots[1].Amount);
        Assert.Equal(new[] { "Ann", "Bob", "Dee" }, report.Pots[1].Eligible);
    }

    [Fact]
    public void UncalledPortionOfARaiseIsRefundedNotWon()
    {
        var table = new Table(new[] { ("Ann", 100), ("Bob", 100) }, 0);
        table.Apply("Bob raise 20\nAnn raise 50\nBob fold");
        var report = table.BuildPots();
        var pot = Assert.Single(report.Pots);
        Assert.Equal(40, pot.Amount); // Bob's 20 plus Ann's matched 20
        Assert.Equal(new[] { "Ann" }, pot.Eligible);
        Assert.Equal(30, report.Refunds["Ann"]); // uncalled top of the raise
        var payouts = table.Distribute(new Dictionary<string, int> { ["Ann"] = 1 });
        Assert.Equal(70, payouts["Ann"]);
        Assert.Equal(0, payouts["Bob"]);
    }

    [Fact]
    public void CheckedDownHandHasNoPots()
    {
        var table = new Table(new[] { ("Ann", 100), ("Bob", 100) }, 0);
        table.Apply("Ann check\nBob check");
        var report = table.BuildPots();
        Assert.Empty(report.Pots);
        Assert.Empty(report.Refunds);
    }
}

public class DistributionTests
{
    [Fact]
    public void EachPotGoesToItsBestEligibleRank()
    {
        var table = new Table(new[] { ("Ann", 100), ("Bob", 60), ("Cid", 30) }, 0);
        table.Apply(Fixtures.Hand("threeway.hand"));
        var payouts = table.Distribute(new Dictionary<string, int>
        {
            ["Cid"] = 1, ["Bob"] = 2, ["Ann"] = 3,
        });
        Assert.Equal(90, payouts["Cid"]);   // main pot
        Assert.Equal(60, payouts["Bob"]);   // side pot
        Assert.Equal(40, payouts["Ann"]);   // refund only
    }

    [Fact]
    public void OverallBestHandSweepsEverything()
    {
        var table = new Table(new[] { ("Ann", 100), ("Bob", 60), ("Cid", 30) }, 0);
        table.Apply(Fixtures.Hand("threeway.hand"));
        var payouts = table.Distribute(new Dictionary<string, int>
        {
            ["Ann"] = 1, ["Bob"] = 2, ["Cid"] = 3,
        });
        Assert.Equal(190, payouts["Ann"]);
        Assert.Equal(0, payouts["Bob"]);
        Assert.Equal(0, payouts["Cid"]);
    }

    [Fact]
    public void TiedRanksSplitThePotEvenly()
    {
        var table = new Table(new[] { ("Ann", 200), ("Bob", 200), ("Cid", 200) }, 2);
        table.Apply("Ann raise 40\nBob call\nCid raise 100\nAnn call\nBob fold");
        var payouts = table.Distribute(new Dictionary<string, int> { ["Ann"] = 1, ["Cid"] = 1 });
        Assert.Equal(120, payouts["Ann"]);
        Assert.Equal(120, payouts["Cid"]);
        Assert.Equal(0, payouts["Bob"]);
    }

    [Fact]
    public void OddChipGoesFirstLeftOfTheButton()
    {
        var table = new Table(new[] { ("Ann", 100), ("Bob", 100), ("Cid", 100) }, 1);
        table.Apply("Ann raise 25\nBob call\nCid call");
        var payouts = table.Distribute(new Dictionary<string, int>
        {
            ["Ann"] = 1, ["Cid"] = 1, ["Bob"] = 2,
        });
        Assert.Equal(38, payouts["Cid"]); // seat 2 is first left of the seat-1 button
        Assert.Equal(37, payouts["Ann"]);
        Assert.Equal(0, payouts["Bob"]);
    }

    [Fact]
    public void ShortStackScenarioPaysMainAndSidePots()
    {
        var table = new Table(new[] { ("Ann", 80), ("Bob", 80), ("Cid", 25), ("Dee", 80) }, 3);
        table.Apply(Fixtures.Hand("shortstack.hand"));
        var payouts = table.Distribute(new Dictionary<string, int>
        {
            ["Cid"] = 1, ["Ann"] = 2, ["Bob"] = 2, ["Dee"] = 3,
        });
        Assert.Equal(100, payouts["Cid"]); // main pot
        Assert.Equal(8, payouts["Ann"]);   // side pot split, odd chip: seat 0 is nearest left of seat-3 button
        Assert.Equal(7, payouts["Bob"]);
        Assert.Equal(0, payouts["Dee"]);
    }

    [Fact]
    public void PayoutsCoverEveryChipCommitted()
    {
        var table = new Table(new[] { ("Ann", 100), ("Bob", 60), ("Cid", 30) }, 0);
        table.Apply(Fixtures.Hand("threeway.hand"));
        var payouts = table.Distribute(new Dictionary<string, int>
        {
            ["Cid"] = 1, ["Bob"] = 1, ["Ann"] = 2,
        });
        Assert.Equal(190, payouts.Values.Sum());
    }

    [Fact]
    public void RanksMustCoverExactlyTheLivePlayers()
    {
        var table = new Table(new[] { ("Ann", 100), ("Bob", 100) }, 0);
        table.Apply("Ann raise 10\nBob call");
        Assert.Throws<ArgumentException>(
            () => table.Distribute(new Dictionary<string, int> { ["Ann"] = 1 }));
        Assert.Throws<ArgumentException>(
            () => table.Distribute(new Dictionary<string, int>
            {
                ["Ann"] = 1, ["Bob"] = 2, ["Zed"] = 3,
            }));
    }
}

internal static class Fixtures
{
    public static string Hand(string name) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "testdata", name));
}
