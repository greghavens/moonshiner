using System.Reflection;

namespace GearRepo;

// Two deliberately different entity shapes: string-keyed radios and
// int-keyed trail cameras. The repository must be generic over both.
internal sealed record RadioUnit(string Id, string Band, bool FieldReady) : IEntity<string>;

internal sealed record TrailCamera(int Id, string Site) : IEntity<int>;

public class CoreRepositoryTests
{
    private static IRepository<RadioUnit, string> NewRepo() =>
        new InMemoryRepository<RadioUnit, string>();

    [Fact]
    public void AddThenTryGet_RoundTrips()
    {
        var repo = NewRepo();
        var radio = new RadioUnit("R-14", "VHF", true);
        repo.Add(radio);

        Assert.True(repo.TryGet("R-14", out var found));
        Assert.Equal(radio, found);
        Assert.Equal(1, repo.Count);
    }

    [Fact]
    public void TryGetUnknownId_ReturnsFalse()
    {
        var repo = NewRepo();
        Assert.False(repo.TryGet("R-99", out var missing));
        Assert.Null(missing);
    }

    [Fact]
    public void AddingAnExistingId_ThrowsDuplicateKey_WithTheIdInTheMessage()
    {
        var repo = NewRepo();
        repo.Add(new RadioUnit("R-14", "VHF", true));

        var ex = Assert.Throws<DuplicateKeyException>(
            () => repo.Add(new RadioUnit("R-14", "UHF", false)));
        Assert.Equal("id 'R-14' already present", ex.Message);
        Assert.Equal(1, repo.Count);
    }

    [Fact]
    public void RemoveReportsWhetherAnythingWasThere()
    {
        var repo = NewRepo();
        repo.Add(new RadioUnit("R-14", "VHF", true));

        Assert.True(repo.Remove("R-14"));
        Assert.False(repo.Remove("R-14"));
        Assert.Equal(0, repo.Count);
        Assert.False(repo.TryGet("R-14", out _));
    }

    [Fact]
    public void ReAddingAfterRemove_IsAllowed_AndMovesToTheEndOfTheOrder()
    {
        var repo = NewRepo();
        repo.Add(new RadioUnit("R-01", "VHF", true));
        repo.Add(new RadioUnit("R-02", "UHF", true));
        repo.Remove("R-01");
        repo.Add(new RadioUnit("R-01", "VHF", false));

        Assert.Equal(new[] { "R-02", "R-01" },
                     repo.Snapshot().Select(r => r.Id).ToArray());
    }

    [Fact]
    public void SnapshotPreservesInsertionOrder()
    {
        var repo = NewRepo();
        repo.Add(new RadioUnit("R-30", "VHF", true));
        repo.Add(new RadioUnit("R-07", "UHF", false));
        repo.Add(new RadioUnit("R-19", "VHF", true));

        Assert.Equal(new[] { "R-30", "R-07", "R-19" },
                     repo.Snapshot().Select(r => r.Id).ToArray());
    }

    [Fact]
    public void SnapshotIsIsolatedFromLaterMutation()
    {
        var repo = NewRepo();
        repo.Add(new RadioUnit("R-30", "VHF", true));
        var snap = repo.Snapshot();

        repo.Add(new RadioUnit("R-07", "UHF", false));
        repo.Remove("R-30");

        Assert.Single(snap);
        Assert.Equal("R-30", snap[0].Id);
    }

    [Fact]
    public void WorksIdenticallyForIntKeyedEntities()
    {
        IRepository<TrailCamera, int> cams = new InMemoryRepository<TrailCamera, int>();
        cams.Add(new TrailCamera(410, "Boulder Creek"));
        cams.Add(new TrailCamera(207, "Windy Gap"));

        Assert.True(cams.TryGet(207, out var cam));
        Assert.Equal("Windy Gap", cam!.Site);
        Assert.False(cams.Remove(999));
        var ex = Assert.Throws<DuplicateKeyException>(
            () => cams.Add(new TrailCamera(410, "Ridge East")));
        Assert.Equal("id '410' already present", ex.Message);
    }
}

public class DefaultMemberBehaviorTests
{
    private static IRepository<RadioUnit, string> Stocked()
    {
        IRepository<RadioUnit, string> repo = new InMemoryRepository<RadioUnit, string>();
        repo.Add(new RadioUnit("R-14", "VHF", true));
        repo.Add(new RadioUnit("R-07", "UHF", false));
        return repo;
    }

    [Fact]
    public void ExistsAnswersWithoutRetrieving()
    {
        var repo = Stocked();
        Assert.True(repo.Exists("R-07"));
        Assert.False(repo.Exists("R-99"));
    }

    [Fact]
    public void GetRequiredReturnsTheEntity()
    {
        var repo = Stocked();
        Assert.Equal(new RadioUnit("R-14", "VHF", true), repo.GetRequired("R-14"));
    }

    [Fact]
    public void GetRequiredOnAMissingId_ThrowsKeyNotFound_WithTheIdInTheMessage()
    {
        var repo = Stocked();
        var ex = Assert.Throws<KeyNotFoundException>(() => repo.GetRequired("R-99"));
        Assert.Equal("no entity with id 'R-99'", ex.Message);
    }

    [Fact]
    public void AddRangeAppendsInSequenceOrder()
    {
        var repo = Stocked();
        repo.AddRange(new[]
        {
            new RadioUnit("R-21", "VHF", true),
            new RadioUnit("R-22", "UHF", true),
        });

        Assert.Equal(new[] { "R-14", "R-07", "R-21", "R-22" },
                     repo.Snapshot().Select(r => r.Id).ToArray());
    }

    [Fact]
    public void AddRangeCollidingWithAStoredId_AddsNothingAtAll()
    {
        var repo = Stocked();
        Assert.Throws<DuplicateKeyException>(() => repo.AddRange(new[]
        {
            new RadioUnit("R-40", "VHF", true),
            new RadioUnit("R-07", "UHF", true),   // already in the depot
            new RadioUnit("R-41", "VHF", true),
        }));

        Assert.Equal(2, repo.Count);              // batch rolled in nothing
        Assert.False(repo.Exists("R-40"));
        Assert.False(repo.Exists("R-41"));
    }

    [Fact]
    public void AddRangeWithADuplicateInsideTheBatch_AddsNothingAtAll()
    {
        var repo = Stocked();
        Assert.Throws<DuplicateKeyException>(() => repo.AddRange(new[]
        {
            new RadioUnit("R-40", "VHF", true),
            new RadioUnit("R-40", "UHF", true),
        }));

        Assert.Equal(2, repo.Count);
        Assert.False(repo.Exists("R-40"));
    }

    [Fact]
    public void FindAllFiltersInInsertionOrder()
    {
        var repo = Stocked();
        repo.Add(new RadioUnit("R-33", "VHF", true));

        var ready = repo.FindAll(r => r.FieldReady);
        Assert.Equal(new[] { "R-14", "R-33" }, ready.Select(r => r.Id).ToArray());
        Assert.Empty(repo.FindAll(r => r.Band == "HF"));
    }
}

public class DefaultMemberShapeTests
{
    // The convenience layer must live ON the interface as default
    // implementations — every storage backend gets it for free.
    [Theory]
    [InlineData("Exists")]
    [InlineData("GetRequired")]
    [InlineData("AddRange")]
    [InlineData("FindAll")]
    public void ConvenienceMembersHaveDefaultBodiesOnTheInterface(string name)
    {
        var method = typeof(IRepository<,>).GetMethod(name, BindingFlags.Public | BindingFlags.Instance);
        Assert.NotNull(method);
        Assert.False(method!.IsAbstract);
        Assert.True(method.IsVirtual);
    }

    [Theory]
    [InlineData("Add")]
    [InlineData("TryGet")]
    [InlineData("Remove")]
    [InlineData("Snapshot")]
    public void StorageMembersStayAbstractOnTheInterface(string name)
    {
        var method = typeof(IRepository<,>).GetMethod(name, BindingFlags.Public | BindingFlags.Instance);
        Assert.NotNull(method);
        Assert.True(method!.IsAbstract);
    }
}

public class AuditTests
{
    [Fact]
    public void MissingIdsKeepsFirstOccurrenceOrderAndDedupes()
    {
        IRepository<RadioUnit, string> repo = new InMemoryRepository<RadioUnit, string>();
        repo.Add(new RadioUnit("R-14", "VHF", true));
        repo.Add(new RadioUnit("R-07", "UHF", false));

        var missing = DepotAudit.MissingIds(repo,
            new[] { "R-99", "R-14", "R-31", "R-99", "R-02" });

        Assert.Equal(new[] { "R-99", "R-31", "R-02" }, missing.ToArray());
    }

    [Fact]
    public void MissingIdsIsEmptyWhenEverythingIsAccountedFor()
    {
        IRepository<TrailCamera, int> cams = new InMemoryRepository<TrailCamera, int>();
        cams.Add(new TrailCamera(410, "Boulder Creek"));

        Assert.Empty(DepotAudit.MissingIds(cams, new[] { 410, 410 }));
        Assert.Empty(DepotAudit.MissingIds(cams, Array.Empty<int>()));
    }
}
