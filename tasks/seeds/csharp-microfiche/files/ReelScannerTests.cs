namespace Microfiche;

/// <summary>
/// In-memory stand-in for the scan appliance. Every ScanBatch call is
/// recorded, because HOW MANY batches get pulled — and when — is the entire
/// contract under test here.
/// </summary>
internal sealed class CountingReelStore : IReelStore
{
    private readonly List<FrameRecord> _all;

    public CountingReelStore(int total) =>
        _all = Enumerable.Range(1, total)
            .Select(i => new FrameRecord($"MF-{i:0000}", $"reel-{i}"))
            .ToList();

    public List<(int Offset, int PageSize)> Scans { get; } = new();

    public IReadOnlyList<FrameRecord> ScanBatch(int offset, int pageSize)
    {
        Scans.Add((offset, pageSize));
        return _all.Skip(offset).Take(pageSize).ToList();
    }
}

public class StreamLazinessTests
{
    [Fact]
    public void BuildingTheStreamTouchesTheApplianceNotAtAll()
    {
        var store = new CountingReelStore(24);
        _ = ReelScanner.Stream(store, 10);
        Assert.Empty(store.Scans);
    }

    [Fact]
    public void FirstFrameCostsExactlyOneBatchScan()
    {
        var store = new CountingReelStore(24);
        var first = ReelScanner.Stream(store, 10).First();

        Assert.Equal(new FrameRecord("MF-0001", "reel-1"), first);
        Assert.Equal(new[] { (0, 10) }, store.Scans);
    }

    [Fact]
    public void ConsumingExactlyOneBatchNeverTouchesTheSecond()
    {
        var store = new CountingReelStore(24);
        var firstTen = ReelScanner.Stream(store, 10).Take(10).ToList();

        Assert.Equal(10, firstTen.Count);
        Assert.Equal(new[] { (0, 10) }, store.Scans);
    }

    [Fact]
    public void TheEleventhFramePullsTheSecondBatchOnDemand()
    {
        var store = new CountingReelStore(24);
        var eleven = ReelScanner.Stream(store, 10).Take(11).ToList();

        Assert.Equal("MF-0011", eleven[^1].FrameId);
        Assert.Equal(new[] { (0, 10), (10, 10) }, store.Scans);
    }

    [Fact]
    public void ShortFinalBatchEndsTheStreamWithoutAnExtraScan()
    {
        var store = new CountingReelStore(24);
        var all = ReelScanner.Stream(store, 10).ToList();

        Assert.Equal(24, all.Count);
        Assert.Equal("MF-0001", all[0].FrameId);
        Assert.Equal("MF-0024", all[^1].FrameId);
        Assert.Equal(
            Enumerable.Range(1, 24).Select(i => $"MF-{i:0000}").ToArray(),
            all.Select(r => r.FrameId).ToArray());
        // batch 3 came back short (4 of 10) -> that IS the end; no (30, 10) scan
        Assert.Equal(new[] { (0, 10), (10, 10), (20, 10) }, store.Scans);
    }

    [Fact]
    public void ExactBatchMultipleNeedsOneEmptyScanToKnowItIsDone()
    {
        var store = new CountingReelStore(20);
        var all = ReelScanner.Stream(store, 10).ToList();

        Assert.Equal(20, all.Count);
        Assert.Equal(new[] { (0, 10), (10, 10), (20, 10) }, store.Scans);
    }

    [Fact]
    public void EmptyArchiveYieldsNothingAfterASingleLook()
    {
        var store = new CountingReelStore(0);
        Assert.Empty(ReelScanner.Stream(store, 10));
        Assert.Equal(new[] { (0, 10) }, store.Scans);
    }

    [Fact]
    public void EachEnumerationScansFreshNoHiddenCaching()
    {
        var store = new CountingReelStore(24);
        var stream = ReelScanner.Stream(store, 10);

        _ = stream.First();
        _ = stream.First();

        Assert.Equal(new[] { (0, 10), (0, 10) }, store.Scans);
    }
}

public class PagesTests
{
    [Fact]
    public void BatchesArriveOneScanAtATime()
    {
        var store = new CountingReelStore(24);
        var firstPage = ReelScanner.Pages(store, 10).First();

        Assert.Equal(10, firstPage.Count);
        Assert.Equal("MF-0001", firstPage[0].FrameId);
        Assert.Equal(new[] { (0, 10) }, store.Scans);
    }

    [Fact]
    public void FullWalkYieldsBatchSizedChunksWithTheShortTailLast()
    {
        var store = new CountingReelStore(24);
        var pages = ReelScanner.Pages(store, 10).ToList();

        Assert.Equal(new[] { 10, 10, 4 }, pages.Select(p => p.Count).ToArray());
        Assert.Equal("MF-0011", pages[1][0].FrameId);
        Assert.Equal("MF-0024", pages[2][^1].FrameId);
        Assert.Equal(new[] { (0, 10), (10, 10), (20, 10) }, store.Scans);
    }

    [Fact]
    public void BuildingPagesIsAlsoCompletelyLazy()
    {
        var store = new CountingReelStore(24);
        _ = ReelScanner.Pages(store, 10);
        Assert.Empty(store.Scans);
    }
}

public class ValidationTests
{
    [Theory]
    [InlineData(0)]
    [InlineData(-3)]
    public void BadPageSizeBlowsUpAtCallTimeNotFirstEnumeration(int pageSize)
    {
        var store = new CountingReelStore(24);
        // no enumeration anywhere below — the throw must happen eagerly
        Assert.Throws<ArgumentOutOfRangeException>(() => ReelScanner.Stream(store, pageSize));
        Assert.Throws<ArgumentOutOfRangeException>(() => ReelScanner.Pages(store, pageSize));
        Assert.Empty(store.Scans);
    }
}

public class FirstMatchTests
{
    [Fact]
    public void MatchInTheFirstBatchStopsAfterOneScan()
    {
        var store = new CountingReelStore(24);
        var hit = ReelScanner.FirstMatch(store, 10, r => r.FrameId == "MF-0003");

        Assert.Equal(new FrameRecord("MF-0003", "reel-3"), hit);
        Assert.Equal(new[] { (0, 10) }, store.Scans);
    }

    [Fact]
    public void MatchDeeperInPullsOnlyTheBatchesBeforeIt()
    {
        var store = new CountingReelStore(24);
        var hit = ReelScanner.FirstMatch(store, 10, r => r.Reel == "reel-17");

        Assert.Equal("MF-0017", hit!.FrameId);
        Assert.Equal(new[] { (0, 10), (10, 10) }, store.Scans);
    }

    [Fact]
    public void NoMatchScansEverythingAndReturnsNull()
    {
        var store = new CountingReelStore(24);
        var hit = ReelScanner.FirstMatch(store, 10, r => r.Reel == "reel-999");

        Assert.Null(hit);
        Assert.Equal(new[] { (0, 10), (10, 10), (20, 10) }, store.Scans);
    }
}
