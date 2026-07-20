using TenantKeyRollout;

var tests = new (string Name, Action Body)[]
{
    ("bounded batches are restartable", BoundedBatchesAreRestartable),
    ("tenant-scoped uniqueness is enforced", TenantScopedUniquenessIsEnforced),
    ("same-tenant collisions block completion", SameTenantCollisionsBlockCompletion),
    ("partial conditional updates are tolerated", PartialConditionalUpdatesAreTolerated),
    ("completion closes the old-writer race", CompletionClosesTheOldWriterRace)
};

var failures = new List<string>();
foreach (var (name, body) in tests)
{
    try
    {
        body();
        Console.WriteLine($"PASS {name}");
    }
    catch (Exception exception)
    {
        failures.Add(name);
        Console.Error.WriteLine($"FAIL {name}: {exception.GetType().Name}: {exception.Message}");
    }
}

if (failures.Count != 0)
{
    Console.Error.WriteLine($"{failures.Count} test(s) failed: {string.Join(", ", failures)}");
    return 1;
}

Console.WriteLine($"All {tests.Length} tests passed.");
return 0;

static void BoundedBatchesAreRestartable()
{
    var store = new InMemoryTenantRecordStore(new[]
    {
        Existing(30, "north", "customer-c"),
        Existing(10, "north", "customer-a"),
        Existing(20, "north", "customer-b")
    });
    var migration = new TenantKeyMigration(store);

    var first = migration.BackfillNextBatch(2);
    Equal(2, first.Scanned, "first batch scan count");
    Equal(2, first.Updated, "first batch update count");
    Equal(0, first.AlreadySet, "first batch already-set count");
    Equal(0, first.ConflictingRecordIds.Count, "first batch conflict count");
    NotNull(store.GetRecord(10).TenantKey, "record 10 should be backfilled");
    NotNull(store.GetRecord(20).TenantKey, "record 20 should be backfilled");
    Null(store.GetRecord(30).TenantKey, "record 30 must be left for the next batch");

    var second = migration.BackfillNextBatch(2);
    Equal(1, second.Scanned, "second batch scan count");
    Equal(1, second.Updated, "second batch update count");

    var final = migration.BackfillNextBatch(2);
    Equal(0, final.Scanned, "completed migration must be idempotent");
    Equal(0, final.Updated, "completed migration must not rewrite records");
}

static void TenantScopedUniquenessIsEnforced()
{
    var store = new InMemoryTenantRecordStore(new[]
    {
        Existing(1, "tenant-a", "invoice-7"),
        Existing(2, "tenant-b", "invoice-7")
    });
    var migration = new TenantKeyMigration(store);

    var batch = migration.BackfillNextBatch(10);
    Equal(2, batch.Updated, "the same external key in different tenants is valid");
    NotEqual(
        store.GetRecord(1).TenantKey,
        store.GetRecord(2).TenantKey,
        "tenant scope must be part of the generated key");

    migration.CompleteMigration();
    store.InsertFromLegacyWriter(3, "tenant-c", "invoice-7");
    Equal(
        TenantScopedKey.Create("tenant-c", "invoice-7"),
        store.GetRecord(3).TenantKey,
        "legacy writes must receive a tenant key after compatibility is enabled");

    Throws<TenantKeyUniqueConstraintException>(
        () => store.InsertFromLegacyWriter(4, "tenant-a", "invoice-7"),
        "a duplicate key inside one tenant must be rejected");
}

static void SameTenantCollisionsBlockCompletion()
{
    var store = new InMemoryTenantRecordStore(new[]
    {
        Existing(1, "tenant-a", "duplicate"),
        Existing(2, "tenant-a", "duplicate")
    });
    var migration = new TenantKeyMigration(store);

    var batch = migration.BackfillNextBatch(10);
    Equal(1, batch.Updated, "one member of the duplicate group can claim the key");
    SequenceEqual(new long[] { 2 }, batch.ConflictingRecordIds, "the collision must be reported");

    var exception = Throws<TenantKeyMigrationNotReadyException>(
        migration.CompleteMigration,
        "completion must reject an unresolved same-tenant collision");
    Equal(1, exception.MissingCount, "the conflicting record remains unmigrated");
    Equal(0, store.ConstraintActivationAttempts, "unsafe constraint DDL must not be attempted");
}

static void PartialConditionalUpdatesAreTolerated()
{
    var inner = new InMemoryTenantRecordStore(new[]
    {
        Existing(1, "tenant-a", "one"),
        Existing(2, "tenant-a", "two"),
        Existing(3, "tenant-a", "three")
    });
    var store = new ConcurrentUpdateStore(inner, recordToUpdate: 2);
    var migration = new TenantKeyMigration(store);

    var batch = migration.BackfillNextBatch(3);
    Equal(3, batch.Scanned, "the original snapshot size is reported");
    Equal(2, batch.Updated, "only updates performed by this batch are counted");
    Equal(1, batch.AlreadySet, "a concurrently migrated row is harmless");
    Equal(0, batch.ConflictingRecordIds.Count, "the concurrent update is not a collision");
    Equal(0, inner.InspectMigrationReadiness().MissingTenantKeyCount, "all rows should be migrated");
}

static void CompletionClosesTheOldWriterRace()
{
    var inner = new InMemoryTenantRecordStore(new[]
    {
        Existing(1, "tenant-a", "already-present")
    });
    var migration = new TenantKeyMigration(new LegacyWriterInterleavingStore(inner));
    migration.BackfillNextBatch(10);

    var notReady = Throws<TenantKeyMigrationNotReadyException>(
        migration.CompleteMigration,
        "an old write racing with completion must be detected before constraint activation");
    Equal(1, notReady.MissingCount, "the interleaved old-writer row is the remaining work");
    Equal(0, inner.ConstraintActivationAttempts, "final constraint activation must not be attempted");
    False(inner.FinalConstraintsActive, "final constraints must remain inactive while work remains");

    var repair = migration.BackfillNextBatch(10);
    Equal(1, repair.Updated, "a retry must backfill the interleaved old-writer row");
    migration.CompleteMigration();
    True(inner.FinalConstraintsActive, "a clean retry should activate the final constraints");

    inner.InsertFromLegacyWriter(3, "tenant-b", "after-cutover");
    Equal(
        TenantScopedKey.Create("tenant-b", "after-cutover"),
        inner.GetRecord(3).TenantKey,
        "old writer traffic remains compatible after cutover");
}

static TenantRecord Existing(long id, string tenantId, string externalKey) =>
    new(id, tenantId, externalKey, TenantKey: null);

static void Equal<T>(T expected, T actual, string message)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new TestFailureException($"{message}: expected '{expected}', got '{actual}'");
    }
}

static void NotEqual<T>(T left, T right, string message)
{
    if (EqualityComparer<T>.Default.Equals(left, right))
    {
        throw new TestFailureException($"{message}: both values were '{left}'");
    }
}

static void NotNull(object? value, string message)
{
    if (value is null)
    {
        throw new TestFailureException(message);
    }
}

static void Null(object? value, string message)
{
    if (value is not null)
    {
        throw new TestFailureException($"{message}: got '{value}'");
    }
}

static void True(bool condition, string message)
{
    if (!condition)
    {
        throw new TestFailureException(message);
    }
}

static void False(bool condition, string message) => True(!condition, message);

static TException Throws<TException>(Action action, string message)
    where TException : Exception
{
    try
    {
        action();
    }
    catch (TException exception)
    {
        return exception;
    }
    catch (Exception exception)
    {
        throw new TestFailureException(
            $"{message}: expected {typeof(TException).Name}, got {exception.GetType().Name}");
    }

    throw new TestFailureException($"{message}: expected {typeof(TException).Name}, but no exception was thrown");
}

static void SequenceEqual<T>(IEnumerable<T> expected, IEnumerable<T> actual, string message)
{
    if (!expected.SequenceEqual(actual))
    {
        throw new TestFailureException(message);
    }
}

sealed class TestFailureException : Exception
{
    public TestFailureException(string message)
        : base(message)
    {
    }
}

sealed class ConcurrentUpdateStore : ITenantRecordStore
{
    private readonly InMemoryTenantRecordStore _inner;
    private readonly long _recordToUpdate;
    private bool _updated;

    public ConcurrentUpdateStore(InMemoryTenantRecordStore inner, long recordToUpdate)
    {
        _inner = inner;
        _recordToUpdate = recordToUpdate;
    }

    public IReadOnlyList<TenantRecord> ReadMissingTenantKeys(int maximumCount) =>
        _inner.ReadMissingTenantKeys(maximumCount);

    public TenantKeyUpdateStatus TrySetTenantKey(long recordId, string tenantKey)
    {
        if (!_updated)
        {
            _updated = true;
            var concurrentRecord = _inner.GetRecord(_recordToUpdate);
            var concurrentKey = TenantScopedKey.Create(
                concurrentRecord.TenantId,
                concurrentRecord.ExternalKey);
            _inner.TrySetTenantKey(_recordToUpdate, concurrentKey);
        }

        return _inner.TrySetTenantKey(recordId, tenantKey);
    }

    public MigrationReadiness InspectMigrationReadiness() =>
        _inner.InspectMigrationReadiness();

    public void EnableLegacyWriterCompatibility() =>
        _inner.EnableLegacyWriterCompatibility();

    public void ActivateFinalTenantKeyConstraints() =>
        _inner.ActivateFinalTenantKeyConstraints();
}

sealed class LegacyWriterInterleavingStore : ITenantRecordStore
{
    private readonly InMemoryTenantRecordStore _inner;
    private bool _writeInjected;

    public LegacyWriterInterleavingStore(InMemoryTenantRecordStore inner)
    {
        _inner = inner;
    }

    public IReadOnlyList<TenantRecord> ReadMissingTenantKeys(int maximumCount) =>
        _inner.ReadMissingTenantKeys(maximumCount);

    public TenantKeyUpdateStatus TrySetTenantKey(long recordId, string tenantKey) =>
        _inner.TrySetTenantKey(recordId, tenantKey);

    public MigrationReadiness InspectMigrationReadiness() =>
        _inner.InspectMigrationReadiness();

    public void EnableLegacyWriterCompatibility()
    {
        if (!_writeInjected)
        {
            _writeInjected = true;
            _inner.InsertFromLegacyWriter(2, "tenant-a", "raced-old-write");
        }

        _inner.EnableLegacyWriterCompatibility();
    }

    public void ActivateFinalTenantKeyConstraints() =>
        _inner.ActivateFinalTenantKeyConstraints();
}
