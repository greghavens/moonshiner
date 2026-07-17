namespace Outfitter;

// Acceptance tests for the reservation-holds + audit-trail feature.
// The console gets a clock injected so every test below is deterministic.
public class HoldsFeatureTests
{
    private static readonly DateTimeOffset Opening = new(2024, 6, 1, 10, 0, 0, TimeSpan.Zero);

    private sealed class TestClock : IClock
    {
        public DateTimeOffset UtcNow { get; set; } = Opening;

        public void Advance(TimeSpan by) => UtcNow += by;
    }

    [Fact]
    public void PlacingAHoldPinsItsReceiptAndReducesAvailability()
    {
        var clock = new TestClock();
        var cli = new OutfitterCli(clock);
        cli.Run("receive kayak-solo 4");

        Assert.Equal(
            "ok: hold H1 holds 2 of kayak-solo until 2024-06-01T11:00:00Z",
            cli.Run("hold kayak-solo 2 60"));
        Assert.Equal("error: only 2 of kayak-solo available (2 on hold)", cli.Run("sell kayak-solo 3"));
        Assert.Equal("ok: kayak-solo now 0", cli.Run("sell kayak-solo 2"));
    }

    [Fact]
    public void StockReportShowsWhatIsOnHold()
    {
        var clock = new TestClock();
        var cli = new OutfitterCli(clock);
        cli.Run("receive kayak-solo 4");
        cli.Run("receive canoe-16 1");
        cli.Run("hold kayak-solo 2 60");

        Assert.Equal("canoe-16 1\nkayak-solo 2 (2 on hold)", cli.Run("stock"));
    }

    [Fact]
    public void ExpiredHoldReturnsStockOnTheNextCommand()
    {
        var clock = new TestClock();
        var cli = new OutfitterCli(clock);
        cli.Run("receive kayak-solo 4");
        cli.Run("hold kayak-solo 2 30");

        clock.Advance(TimeSpan.FromMinutes(31));
        Assert.Equal("kayak-solo 4", cli.Run("stock"));
        Assert.Equal("(none)", cli.Run("holds"));
        Assert.EndsWith("2024-06-01T10:31:00Z expire H1", cli.Run("audit"));
    }

    [Fact]
    public void ExpiryBoundaryIsInclusive()
    {
        var clock = new TestClock();
        var cli = new OutfitterCli(clock);
        cli.Run("receive kayak-solo 4");
        cli.Run("hold kayak-solo 2 30");

        clock.Advance(TimeSpan.FromMinutes(30));
        Assert.Equal("(none)", cli.Run("holds"));
        Assert.Equal("kayak-solo 4", cli.Run("stock"));
    }

    [Fact]
    public void ReleaseReturnsStockAndIsSingleShot()
    {
        var clock = new TestClock();
        var cli = new OutfitterCli(clock);
        cli.Run("receive kayak-solo 4");
        cli.Run("hold kayak-solo 2 60");

        Assert.Equal("ok: hold H1 released", cli.Run("release H1"));
        Assert.Equal("kayak-solo 4", cli.Run("stock"));
        Assert.Equal("error: unknown hold 'H1'", cli.Run("release H1"));
    }

    [Fact]
    public void HoldIdsAreSequentialAndListedInPlacementOrder()
    {
        var clock = new TestClock();
        var cli = new OutfitterCli(clock);
        cli.Run("receive kayak-solo 5");
        cli.Run("receive canoe-16 2");
        cli.Run("hold kayak-solo 1 60");
        cli.Run("hold canoe-16 1 45");
        cli.Run("hold kayak-solo 2 30");

        Assert.Equal(
            "H1 kayak-solo x1 until 2024-06-01T11:00:00Z\n" +
            "H2 canoe-16 x1 until 2024-06-01T10:45:00Z\n" +
            "H3 kayak-solo x2 until 2024-06-01T10:30:00Z",
            cli.Run("holds"));
    }

    [Fact]
    public void AuditTrailRecordsMutationsWithClockTimestamps()
    {
        var clock = new TestClock();
        var cli = new OutfitterCli(clock);
        cli.Run("receive kayak-solo 4");
        clock.Advance(TimeSpan.FromMinutes(5));
        cli.Run("sell kayak-solo 1");
        clock.Advance(TimeSpan.FromMinutes(5));
        cli.Run("hold kayak-solo 2 60");
        clock.Advance(TimeSpan.FromMinutes(2));
        cli.Run("release H1");

        var expected =
            "2024-06-01T10:00:00Z receive kayak-solo 4\n" +
            "2024-06-01T10:05:00Z sell kayak-solo 1\n" +
            "2024-06-01T10:10:00Z hold H1 kayak-solo 2 until 2024-06-01T11:10:00Z\n" +
            "2024-06-01T10:12:00Z release H1";
        Assert.Equal(expected, cli.Run("audit"));

        // refused commands leave no audit entries
        cli.Run("sell kayak-solo 99");
        Assert.Equal(expected, cli.Run("audit"));
    }

    [Fact]
    public void FreshConsoleHasAnEmptyAuditTrail()
    {
        var cli = new OutfitterCli(new TestClock());
        Assert.Equal("(none)", cli.Run("audit"));
        Assert.Equal("(none)", cli.Run("holds"));
    }

    [Fact]
    public void HoldValidationMessages()
    {
        var clock = new TestClock();
        var cli = new OutfitterCli(clock);
        Assert.Equal("error: unknown sku 'paddle-sup'", cli.Run("hold paddle-sup 1 60"));

        cli.Run("receive kayak-solo 4");
        Assert.Equal("error: only 4 of kayak-solo available", cli.Run("hold kayak-solo 5 60"));
        Assert.Equal("error: minutes must be a positive integer", cli.Run("hold kayak-solo 2 0"));
        Assert.Equal("error: quantity must be a positive integer", cli.Run("hold kayak-solo 0 60"));
        Assert.Equal("error: usage: hold <sku> <qty> <minutes>", cli.Run("hold kayak-solo 2"));
        Assert.Equal("error: usage: release <hold-id>", cli.Run("release"));
    }

    [Fact]
    public void HoldingMoreThanTheUnheldRemainderFails()
    {
        var clock = new TestClock();
        var cli = new OutfitterCli(clock);
        cli.Run("receive kayak-solo 4");
        cli.Run("hold kayak-solo 3 60");

        Assert.Equal("error: only 1 of kayak-solo available (3 on hold)", cli.Run("hold kayak-solo 2 60"));
    }
}
