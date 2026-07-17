namespace OrchardSales;

public class CoopReportTests
{
    // One good receiving week at the dock, chosen so the tie-break rules and
    // the crates-vs-revenue distinction actually matter. Do not reorder:
    // one assertion feeds these back in reverse to prove order independence.
    private static List<Delivery> Week() =>
    [
        new("G-ANDER", "North Slope", new DateOnly(2025, 9, 1),
            [new("Honeycrisp", 10, 42.00m), new("Gala", 6, 30.00m)]),
        new("G-BILTON", "Riverbend", new DateOnly(2025, 9, 1),
            [new("Gala", 8, 31.00m), new("Fuji", 5, 28.00m), new("Honeycrisp", 4, 40.00m)]),
        new("G-CRUZ", "Mesa Vieja", new DateOnly(2025, 9, 2),
            [new("Honeycrisp", 4, 45.00m)]),
        new("G-ANDER", "North Slope", new DateOnly(2025, 9, 2),
            [new("Fuji", 10, 27.00m), new("Gala", 2, 30.00m)]),
        new("G-DELMAR", "Coldwater", new DateOnly(2025, 9, 2), []),
        new("G-BILTON", "Riverbend", new DateOnly(2025, 9, 3),
            [new("Honeycrisp", 6, 44.00m), new("Opal", 3, 38.00m)]),
        new("G-CRUZ", "Mesa Vieja", new DateOnly(2025, 9, 3),
            [new("Gala", 4, 29.50m), new("Fuji", 7, 28.00m)]),
        new("G-ANDER", "High Field", new DateOnly(2025, 9, 3),
            [new("Opal", 5, 38.00m)]),
        new("G-ELKIN", "Dry Bench", new DateOnly(2025, 9, 1), []),
    ];

    [Fact]
    public void VarietyTotals_AggregatesAcrossAllDeliveries_RevenueDescending()
    {
        var totals = CoopReport.VarietyTotals(Week());
        // Fuji moves MORE crates than Gala (22 vs 20) but earns the same
        // revenue; ordering is by revenue first, then variety name (ordinal).
        var expected = new VarietyTotal[]
        {
            new("Honeycrisp", 24, 1024.00m),
            new("Fuji", 22, 606.00m),
            new("Gala", 20, 606.00m),
            new("Opal", 8, 304.00m),
        };
        Assert.Equal(expected, totals);
    }

    [Fact]
    public void VarietyTotals_EmptyInput_YieldsEmptyList()
    {
        Assert.Empty(CoopReport.VarietyTotals([]));
    }

    [Fact]
    public void GrowerLeaderboard_RanksByRevenue_ThenGrowerIdForTies()
    {
        var board = CoopReport.GrowerLeaderboard(Week(), 5);
        var expected = new GrowerSummary[]
        {
            new("G-ANDER", 1120.00m, 3, 4),
            new("G-BILTON", 926.00m, 2, 4),
            new("G-CRUZ", 494.00m, 2, 3),
            new("G-DELMAR", 0m, 1, 0),
            new("G-ELKIN", 0m, 1, 0),
        };
        Assert.Equal(expected, board);
    }

    [Fact]
    public void GrowerLeaderboard_TakesOnlyTopN()
    {
        var board = CoopReport.GrowerLeaderboard(Week(), 2);
        Assert.Equal(new[] { "G-ANDER", "G-BILTON" }, board.Select(g => g.GrowerId).ToArray());
    }

    [Fact]
    public void GrowerLeaderboard_TopLargerThanGrowerCount_ReturnsEveryone()
    {
        Assert.Equal(5, CoopReport.GrowerLeaderboard(Week(), 50).Count);
    }

    [Fact]
    public void GrowerLeaderboard_TopZero_IsEmpty()
    {
        Assert.Empty(CoopReport.GrowerLeaderboard(Week(), 0));
    }

    [Fact]
    public void GrowerLeaderboard_NegativeTop_Throws()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => CoopReport.GrowerLeaderboard(Week(), -1));
    }

    [Fact]
    public void DailyTotals_OrderedByDay_TopVarietyByCratesWithOrdinalTieBreak()
    {
        var days = CoopReport.DailyTotals(Week());
        // Sep 1: Gala and Honeycrisp both land 14 crates -> "Gala" wins ordinally.
        var expected = new DailyTotal[]
        {
            new(new DateOnly(2025, 9, 1), 1148.00m, "Gala"),
            new(new DateOnly(2025, 9, 2), 510.00m, "Fuji"),
            new(new DateOnly(2025, 9, 3), 882.00m, "Opal"),
        };
        Assert.Equal(expected, days);
    }

    [Fact]
    public void DailyTotals_DayWithOnlyEmptyTickets_ReportsZeroAndBlankTopVariety()
    {
        var days = CoopReport.DailyTotals(
        [
            new Delivery("G-DELMAR", "Coldwater", new DateOnly(2025, 9, 4), []),
            new Delivery("G-ELKIN", "Dry Bench", new DateOnly(2025, 9, 4), []),
        ]);
        Assert.Equal(new[] { new DailyTotal(new DateOnly(2025, 9, 4), 0m, "") }, days);
    }

    [Fact]
    public void DailyTotals_EmptyInput_YieldsEmptyList()
    {
        Assert.Empty(CoopReport.DailyTotals([]));
    }

    [Fact]
    public void SpreadFor_ReportsMinMaxAndCrateWeightedAverage()
    {
        Assert.Equal(new PriceSpread(40.00m, 45.00m, 42.67m),
                     CoopReport.SpreadFor(Week(), "Honeycrisp"));
        Assert.Equal(new PriceSpread(29.50m, 31.00m, 30.30m),
                     CoopReport.SpreadFor(Week(), "Gala"));
    }

    [Fact]
    public void SpreadFor_WeightedAverageRoundsMidpointsAwayFromZero()
    {
        var deliveries = new List<Delivery>
        {
            new("G-ANDER", "North Slope", new DateOnly(2025, 9, 5),
                [new("Jonagold", 1, 10.09m), new("Jonagold", 1, 10.00m)]),
        };
        // 20.09 / 2 crates = 10.045 -> 10.05, not banker's 10.04.
        Assert.Equal(new PriceSpread(10.00m, 10.09m, 10.05m),
                     CoopReport.SpreadFor(deliveries, "Jonagold"));
    }

    [Fact]
    public void SpreadFor_UnknownVariety_ThrowsWithVarietyName()
    {
        var ex = Assert.Throws<InvalidOperationException>(() => CoopReport.SpreadFor(Week(), "Mutsu"));
        Assert.Equal("no crates of variety 'Mutsu'", ex.Message);
    }

    [Fact]
    public void ReportsDoNotDependOnDeliveryOrder()
    {
        var reversed = Week();
        reversed.Reverse();
        Assert.Equal(CoopReport.VarietyTotals(Week()), CoopReport.VarietyTotals(reversed));
        Assert.Equal(CoopReport.GrowerLeaderboard(Week(), 5), CoopReport.GrowerLeaderboard(reversed, 5));
        Assert.Equal(CoopReport.DailyTotals(Week()), CoopReport.DailyTotals(reversed));
    }
}
