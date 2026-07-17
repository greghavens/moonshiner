using System.Reflection;

namespace PatchBay;

public class LeaseLifecycleTests
{
    [Fact]
    public void AcquireMarksUnitBusyAndDisposeFrees()
    {
        var board = new GearBoard("comp-1176", "eq-550a");

        var lease = board.Acquire("comp-1176", "ep-mixdown");
        Assert.Equal("comp-1176", lease.Unit);
        Assert.Equal("ep-mixdown", lease.Session);
        Assert.False(lease.IsReleased);
        Assert.Equal(1, board.ActiveCount);

        lease.Dispose();
        Assert.True(lease.IsReleased);
        Assert.Equal(0, board.ActiveCount);
    }

    [Fact]
    public void LeasingABusyUnitNamesTheSessionHoldingIt()
    {
        var board = new GearBoard("comp-1176");
        using var held = board.Acquire("comp-1176", "ep-mixdown");

        var ex = Assert.Throws<InvalidOperationException>(() => board.Acquire("comp-1176", "single-tracking"));
        Assert.Equal("unit 'comp-1176' is already leased to session 'ep-mixdown'", ex.Message);
    }

    [Fact]
    public void UnknownUnitIsRejected()
    {
        var board = new GearBoard("comp-1176");

        var ex = Assert.Throws<KeyNotFoundException>(() => board.Acquire("tape-delay", "ep-mixdown"));
        Assert.Equal("no such unit 'tape-delay'", ex.Message);
    }

    [Fact]
    public void DuplicateUnitInTheRackIsRejected()
    {
        var ex = Assert.Throws<ArgumentException>(() => new GearBoard("comp-1176", "eq-550a", "comp-1176"));
        Assert.StartsWith("duplicate unit 'comp-1176'", ex.Message);
    }

    [Fact]
    public void ReleasedUnitCanBeLeasedByTheNextSession()
    {
        var board = new GearBoard("comp-1176");
        using (board.Acquire("comp-1176", "ep-mixdown"))
        {
        }

        using var next = board.Acquire("comp-1176", "single-tracking");
        Assert.Equal("single-tracking", next.Session);
        Assert.Equal(1, board.ActiveCount);
    }
}

public class DisposeDisciplineTests
{
    [Fact]
    public void DisposingTwiceReleasesExactlyOnce()
    {
        var board = new GearBoard("comp-1176");
        var lease = board.Acquire("comp-1176", "ep-mixdown");

        lease.Dispose();
        lease.Dispose();
        lease.Dispose();

        Assert.Equal(new[] { "comp-1176:ep-mixdown" }, board.ReleaseLog);
        Assert.Equal(0, board.ActiveCount);
    }

    [Fact]
    public void NestedUsingScopesReleaseInLifoOrder()
    {
        var board = new GearBoard("comp-1176", "eq-550a", "verb-140");

        using (board.Acquire("comp-1176", "ep-mixdown"))
        using (board.Acquire("eq-550a", "ep-mixdown"))
        using (board.Acquire("verb-140", "ep-mixdown"))
        {
            Assert.Equal(3, board.ActiveCount);
        }

        Assert.Equal(
            new[] { "verb-140:ep-mixdown", "eq-550a:ep-mixdown", "comp-1176:ep-mixdown" },
            board.ReleaseLog);
    }

    [Fact]
    public void RoutingThroughALiveLeaseWorks()
    {
        var board = new GearBoard("comp-1176");
        using var lease = board.Acquire("comp-1176", "ep-mixdown");

        Assert.Equal("comp-1176<-ch12", lease.Route("ch12"));
    }

    [Fact]
    public void RoutingThroughAReleasedLeaseThrowsObjectDisposed()
    {
        var board = new GearBoard("comp-1176");
        var lease = board.Acquire("comp-1176", "ep-mixdown");
        lease.Dispose();

        var ex = Assert.Throws<ObjectDisposedException>(() => lease.Route("ch12"));
        Assert.Equal(nameof(GearLease), ex.ObjectName);
    }

    [Fact]
    public void LeaseTypeCarriesNoFinalizer()
    {
        // Cleanup is deterministic disposal only — a finalizer would hide leaks
        // from the leak report and drag the units through an extra GC generation.
        var finalizer = typeof(GearLease).GetMethod(
            "Finalize", BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly);
        Assert.Null(finalizer);
    }
}

public class BoardReportingTests
{
    [Fact]
    public void ActiveUnitsAreReportedInOrdinalOrder()
    {
        var board = new GearBoard("verb-140", "EQ-550a", "comp-1176");
        using var a = board.Acquire("verb-140", "ep-mixdown");
        using var b = board.Acquire("comp-1176", "ep-mixdown");
        using var c = board.Acquire("EQ-550a", "single-tracking");

        Assert.Equal(new[] { "EQ-550a", "comp-1176", "verb-140" }, board.ActiveUnits);
    }

    [Fact]
    public void AssertAllReleasedIsQuietWhenEverythingCameBack()
    {
        var board = new GearBoard("comp-1176", "eq-550a");
        using (board.Acquire("comp-1176", "ep-mixdown"))
        {
        }

        board.AssertAllReleased();
    }

    [Fact]
    public void AssertAllReleasedNamesTheLeaksInOrdinalOrder()
    {
        var board = new GearBoard("verb-140", "EQ-550a", "comp-1176");
        var leak1 = board.Acquire("verb-140", "ep-mixdown");
        var leak2 = board.Acquire("EQ-550a", "single-tracking");

        var ex = Assert.Throws<InvalidOperationException>(() => board.AssertAllReleased());
        Assert.Equal("unreleased units: EQ-550a, verb-140", ex.Message);

        leak1.Dispose();
        leak2.Dispose();
        board.AssertAllReleased();
    }
}
