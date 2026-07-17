using CityDocks;

public class DockLedgerTests
{
    [Fact]
    public void NewDockIsEmptyAndFullyAvailable()
    {
        var dock = new DockLedger("5th & Pine", 8);
        Assert.Equal("5th & Pine", dock.Station);
        Assert.Equal(8, dock.Available);
        Assert.Empty(dock.Movements);
    }

    [Fact]
    public void CapacityUnderOneIsRejected()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new DockLedger("Depot", 0));
    }

    [Fact]
    public void DockingFillsBaysAndLogsArrivals()
    {
        var dock = new DockLedger("Canal St", 3);
        Assert.True(dock.Dock("B-101"));
        Assert.True(dock.Dock("B-207"));
        Assert.Equal(1, dock.Available);
        Assert.Equal(new[] { "IN  B-101", "IN  B-207" }, dock.Movements);
    }

    [Fact]
    public void FullDockRefusesArrivals()
    {
        var dock = new DockLedger("Canal St", 1);
        Assert.True(dock.Dock("B-101"));
        Assert.False(dock.Dock("B-207"));
        Assert.Equal(0, dock.Available);
        Assert.Single(dock.Movements);
    }

    [Fact]
    public void SameTagCannotDockTwice()
    {
        var dock = new DockLedger("Canal St", 4);
        Assert.True(dock.Dock("B-101"));
        Assert.False(dock.Dock("B-101"));
        Assert.Equal(3, dock.Available);
    }

    [Fact]
    public void ReleaseFreesTheBayAndLogsDeparture()
    {
        var dock = new DockLedger("Canal St", 2);
        dock.Dock("B-101");
        dock.Dock("B-207");
        Assert.True(dock.Release("B-101"));
        Assert.Equal(1, dock.Available);
        Assert.Equal(new[] { "IN  B-101", "IN  B-207", "OUT B-101" }, dock.Movements);
    }

    [Fact]
    public void ReleasingAnAbsentTagChangesNothing()
    {
        var dock = new DockLedger("Canal St", 2);
        dock.Dock("B-101");
        Assert.False(dock.Release("B-999"));
        Assert.Equal(1, dock.Available);
        Assert.Equal(new[] { "IN  B-101" }, dock.Movements);
    }

    [Fact]
    public void ReleasedTagCanDockAgain()
    {
        var dock = new DockLedger("Canal St", 1);
        Assert.True(dock.Dock("B-101"));
        Assert.True(dock.Release("B-101"));
        Assert.True(dock.Dock("B-101"));
        Assert.Equal(0, dock.Available);
    }

    [Fact]
    public void UtilizationRoundsToOneDecimal()
    {
        var dock = new DockLedger("Canal St", 3);
        dock.Dock("B-101");
        Assert.Equal(33.3m, dock.UtilizationPercent);
        dock.Dock("B-207");
        Assert.Equal(66.7m, dock.UtilizationPercent);
    }

    [Fact]
    public void EmptyDockHasZeroUtilization()
    {
        var dock = new DockLedger("Canal St", 5);
        Assert.Equal(0m, dock.UtilizationPercent);
    }
}
