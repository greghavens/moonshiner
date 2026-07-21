using MobileOfflineOutbox;

var tests = new (string Name, Func<Task> Body)[]
{
    ("accepted edits drain in durable order", AcceptedEditsDrainInOrder),
    ("conflict remains visible across restart", ConflictRemainsVisibleAcrossRestart),
    ("retry-later preserves head identity", RetryLaterPreservesHeadIdentity),
    ("lost response retries safely after restart", LostResponseRetriesSafely),
    ("cancellation leaves the in-flight edit queued", CancellationLeavesEditQueued),
    ("restart preserves sequence allocation", RestartPreservesSequenceAllocation)
};

int failed = 0;
foreach ((string name, Func<Task> body) in tests)
{
    try
    {
        await body();
        Console.WriteLine($"PASS {name}");
    }
    catch (Exception exception)
    {
        failed++;
        Console.Error.WriteLine($"FAIL {name}: {exception.Message}");
    }
}

Console.WriteLine($"{tests.Length - failed}/{tests.Length} tests passed");
return failed == 0 ? 0 : 1;

static async Task AcceptedEditsDrainInOrder()
{
    using var file = new TemporaryOutboxFile();
    Guid firstId = Guid.Parse("10000000-0000-0000-0000-000000000001");
    Guid secondId = Guid.Parse("10000000-0000-0000-0000-000000000002");
    var store = new JsonFileOutboxStore(
        file.Path,
        Ids(firstId, secondId));
    OutboxEntry first = store.Enqueue("note-7", 3, "first offline edit");
    OutboxEntry second = store.Enqueue("note-7", 4, "second offline edit");
    var transport = new ScriptedTransport(
        (_, _, _) => Task.FromResult(PushResult.Applied));

    ReconcileResult result = await new OutboxReconciler(store, transport)
        .ReconcileAsync();

    Check.Equal(ReconcileStopReason.Drained, result.StopReason);
    Check.Equal(2, result.AcknowledgedCount);
    Check.SequenceEqual(new[] { firstId, secondId },
        transport.Calls.Select(entry => entry.OperationId));
    Check.SequenceEqual(new[] { first.Sequence, second.Sequence },
        transport.Calls.Select(entry => entry.Sequence));
    Check.Equal(0, store.Snapshot().Count);
}

static async Task ConflictRemainsVisibleAcrossRestart()
{
    using var file = new TemporaryOutboxFile();
    Guid conflictId = Guid.Parse("20000000-0000-0000-0000-000000000001");
    Guid laterId = Guid.Parse("20000000-0000-0000-0000-000000000002");
    Guid resolvedId = Guid.Parse("20000000-0000-0000-0000-000000000003");
    var store = new JsonFileOutboxStore(
        file.Path,
        Ids(conflictId, laterId));
    store.Enqueue("profile", 5, "offline display name");
    store.Enqueue("settings", 2, "dark mode");
    var conflictTransport = new ScriptedTransport(
        (_, _, _) => Task.FromResult(
            PushResult.Conflicted(8, "server display name")));

    ReconcileResult result = await new OutboxReconciler(store, conflictTransport)
        .ReconcileAsync();

    Check.Equal(ReconcileStopReason.Conflict, result.StopReason);
    Check.Equal(0, result.AcknowledgedCount);
    Check.Equal(1, conflictTransport.Calls.Count);

    IReadOnlyList<OutboxEntry> visible = store.Snapshot();
    Check.Equal(2, visible.Count);
    Check.Equal(conflictId, visible[0].OperationId);
    Check.Equal(OutboxEntryState.Conflict, visible[0].State);
    Check.NotNull(visible[0].Conflict);
    Check.Equal(8, visible[0].Conflict!.ServerVersion);
    Check.Equal("server display name", visible[0].Conflict!.ServerBody);

    var restarted = new JsonFileOutboxStore(file.Path, Ids(resolvedId));
    var mustNotSend = new ScriptedTransport(
        (_, _, _) => throw new InvalidOperationException(
            "A durable conflict must block later edits."));
    ReconcileResult blocked = await new OutboxReconciler(restarted, mustNotSend)
        .ReconcileAsync();

    Check.Equal(ReconcileStopReason.Conflict, blocked.StopReason);
    Check.Equal(0, blocked.AcknowledgedCount);
    Check.Equal(0, mustNotSend.Calls.Count);

    OutboxEntry resolved = restarted.ResolveConflict(
        conflictId,
        newBaseVersion: 8,
        mergedBody: "merged display name");
    Check.Equal(resolvedId, resolved.OperationId);
    Check.Equal(visible[0].Sequence, resolved.Sequence);
    Check.True(resolved.OperationId != conflictId);
    Check.Equal(8, resolved.BaseVersion);
    Check.Equal("merged display name", resolved.Body);
    Check.Equal(OutboxEntryState.Pending, resolved.State);
    Check.True(resolved.Conflict is null);

    var retryReplacement = new ScriptedTransport(
        (_, _, _) => Task.FromResult(PushResult.RetryLater));
    ReconcileResult replacementPaused = await new OutboxReconciler(
            restarted,
            retryReplacement)
        .ReconcileAsync();

    Check.Equal(ReconcileStopReason.RetryLater, replacementPaused.StopReason);
    Check.Equal(0, replacementPaused.AcknowledgedCount);
    Check.SequenceEqual(new[] { resolvedId },
        retryReplacement.Calls.Select(entry => entry.OperationId));
    Check.SequenceEqual(new[] { resolvedId, laterId },
        restarted.Snapshot().Select(entry => entry.OperationId));

    var afterResolutionRestart = new JsonFileOutboxStore(file.Path);
    var accepting = new ScriptedTransport(
        (_, _, _) => Task.FromResult(PushResult.Applied));
    ReconcileResult completed = await new OutboxReconciler(
            afterResolutionRestart,
            accepting)
        .ReconcileAsync();

    Check.Equal(ReconcileStopReason.Drained, completed.StopReason);
    Check.SequenceEqual(new[] { resolvedId, laterId },
        accepting.Calls.Select(entry => entry.OperationId));
}

static async Task RetryLaterPreservesHeadIdentity()
{
    using var file = new TemporaryOutboxFile();
    Guid firstId = Guid.Parse("30000000-0000-0000-0000-000000000001");
    Guid secondId = Guid.Parse("30000000-0000-0000-0000-000000000002");
    var store = new JsonFileOutboxStore(
        file.Path,
        Ids(firstId, secondId));
    store.Enqueue("draft", 1, "edit A");
    store.Enqueue("draft", 2, "edit B");
    var unavailable = new ScriptedTransport(
        (_, _, _) => Task.FromResult(PushResult.RetryLater));

    ReconcileResult paused = await new OutboxReconciler(store, unavailable)
        .ReconcileAsync();

    Check.Equal(ReconcileStopReason.RetryLater, paused.StopReason);
    Check.Equal(0, paused.AcknowledgedCount);
    Check.SequenceEqual(new[] { firstId, secondId },
        store.Snapshot().Select(entry => entry.OperationId));

    var restarted = new JsonFileOutboxStore(file.Path);
    var accepting = new ScriptedTransport(
        (entry, call, _) => Task.FromResult(
            call == 0 ? PushResult.AlreadyApplied : PushResult.Applied));
    ReconcileResult completed = await new OutboxReconciler(restarted, accepting)
        .ReconcileAsync();

    Check.Equal(ReconcileStopReason.Drained, completed.StopReason);
    Check.Equal(2, completed.AcknowledgedCount);
    Check.SequenceEqual(new[] { firstId, secondId },
        accepting.Calls.Select(entry => entry.OperationId));
}

static async Task LostResponseRetriesSafely()
{
    using var file = new TemporaryOutboxFile();
    Guid operationId = Guid.Parse("40000000-0000-0000-0000-000000000001");
    var store = new JsonFileOutboxStore(file.Path, Ids(operationId));
    store.Enqueue("checklist", 10, "completed item");
    var server = new ApplyThenLoseResponseTransport();

    await Check.ThrowsAsync<IOException>(
        () => new OutboxReconciler(store, server).ReconcileAsync());

    Check.Equal(operationId, Check.Single(store.Snapshot()).OperationId);

    var restarted = new JsonFileOutboxStore(file.Path);
    ReconcileResult completed = await new OutboxReconciler(restarted, server)
        .ReconcileAsync();

    Check.Equal(ReconcileStopReason.Drained, completed.StopReason);
    Check.Equal(1, completed.AcknowledgedCount);
    Check.SequenceEqual(new[] { operationId, operationId },
        server.Calls.Select(entry => entry.OperationId));
    Check.Equal(1, server.AppliedCount);
    Check.Equal(0, restarted.Snapshot().Count);
}

static async Task CancellationLeavesEditQueued()
{
    using var file = new TemporaryOutboxFile();
    Guid operationId = Guid.Parse("50000000-0000-0000-0000-000000000001");
    var store = new JsonFileOutboxStore(file.Path, Ids(operationId));
    store.Enqueue("journal", 0, "offline entry");
    using var cancellation = new CancellationTokenSource();
    var transport = new ScriptedTransport((_, _, _) =>
    {
        cancellation.Cancel();
        return Task.FromResult(PushResult.Applied);
    });

    await Check.ThrowsAsync<OperationCanceledException>(
        () => new OutboxReconciler(store, transport)
            .ReconcileAsync(cancellation.Token));

    Check.Equal(operationId, Check.Single(store.Snapshot()).OperationId);
    var restarted = new JsonFileOutboxStore(file.Path);
    Check.Equal(operationId, Check.Single(restarted.Snapshot()).OperationId);

    var accepting = new ScriptedTransport(
        (_, _, _) => Task.FromResult(PushResult.Applied));
    ReconcileResult completed = await new OutboxReconciler(restarted, accepting)
        .ReconcileAsync();

    Check.Equal(ReconcileStopReason.Drained, completed.StopReason);
    Check.Equal(1, completed.AcknowledgedCount);
    Check.SequenceEqual(new[] { operationId },
        accepting.Calls.Select(entry => entry.OperationId));
}

static Task RestartPreservesSequenceAllocation()
{
    using var file = new TemporaryOutboxFile();
    Guid firstId = Guid.Parse("60000000-0000-0000-0000-000000000001");
    Guid secondId = Guid.Parse("60000000-0000-0000-0000-000000000002");
    var firstProcess = new JsonFileOutboxStore(file.Path, Ids(firstId));
    OutboxEntry first = firstProcess.Enqueue("map", 1, "pin one");

    var secondProcess = new JsonFileOutboxStore(file.Path, Ids(secondId));
    OutboxEntry second = secondProcess.Enqueue("map", 2, "pin two");

    Check.Equal(0L, first.Sequence);
    Check.Equal(1L, second.Sequence);
    Check.SequenceEqual(new[] { firstId, secondId },
        secondProcess.Snapshot().Select(entry => entry.OperationId));
    return Task.CompletedTask;
}

static Func<Guid> Ids(params Guid[] ids)
{
    var remaining = new Queue<Guid>(ids);
    return () => remaining.Count > 0
        ? remaining.Dequeue()
        : throw new InvalidOperationException("The test operation-id source is exhausted.");
}

internal sealed class ScriptedTransport : IEditTransport
{
    private readonly Func<OutboxEntry, int, CancellationToken, Task<PushResult>> _handler;

    public ScriptedTransport(
        Func<OutboxEntry, int, CancellationToken, Task<PushResult>> handler)
    {
        _handler = handler;
    }

    public List<OutboxEntry> Calls { get; } = new();

    public Task<PushResult> PushAsync(
        OutboxEntry entry,
        CancellationToken cancellationToken)
    {
        int call = Calls.Count;
        Calls.Add(entry);
        return _handler(entry, call, cancellationToken);
    }
}

internal sealed class ApplyThenLoseResponseTransport : IEditTransport
{
    private readonly HashSet<Guid> _applied = new();
    private bool _loseNextResponse = true;

    public List<OutboxEntry> Calls { get; } = new();

    public int AppliedCount => _applied.Count;

    public Task<PushResult> PushAsync(
        OutboxEntry entry,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Calls.Add(entry);
        bool newlyApplied = _applied.Add(entry.OperationId);

        if (_loseNextResponse)
        {
            _loseNextResponse = false;
            throw new IOException("The response was lost after the server commit.");
        }

        return Task.FromResult(
            newlyApplied ? PushResult.Applied : PushResult.AlreadyApplied);
    }
}

internal sealed class TemporaryOutboxFile : IDisposable
{
    public TemporaryOutboxFile()
    {
        string directory = System.IO.Path.Combine(
            System.IO.Path.GetTempPath(),
            "mobile-offline-outbox-tests",
            Guid.NewGuid().ToString("N"));
        Path = System.IO.Path.Combine(directory, "outbox.json");
    }

    public string Path { get; }

    public void Dispose()
    {
        string? directory = System.IO.Path.GetDirectoryName(Path);
        if (directory is not null && Directory.Exists(directory))
        {
            Directory.Delete(directory, recursive: true);
        }
    }
}

internal static class Check
{
    public static void Equal<T>(T expected, T actual)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new InvalidOperationException(
                $"Expected <{expected}> but found <{actual}>.");
        }
    }

    public static void SequenceEqual<T>(
        IEnumerable<T> expected,
        IEnumerable<T> actual)
    {
        if (!expected.SequenceEqual(actual))
        {
            throw new InvalidOperationException(
                $"Expected [{string.Join(", ", expected)}] " +
                $"but found [{string.Join(", ", actual)}].");
        }
    }

    public static void True(bool condition)
    {
        if (!condition)
        {
            throw new InvalidOperationException("Expected condition to be true.");
        }
    }

    public static void NotNull(object? value)
    {
        if (value is null)
        {
            throw new InvalidOperationException("Expected a non-null value.");
        }
    }

    public static T Single<T>(IEnumerable<T> values)
    {
        T[] materialized = values.ToArray();
        if (materialized.Length != 1)
        {
            throw new InvalidOperationException(
                $"Expected one value but found {materialized.Length}.");
        }

        return materialized[0];
    }

    public static async Task ThrowsAsync<TException>(Func<Task> action)
        where TException : Exception
    {
        try
        {
            await action();
        }
        catch (TException)
        {
            return;
        }

        throw new InvalidOperationException(
            $"Expected {typeof(TException).Name}.");
    }
}
