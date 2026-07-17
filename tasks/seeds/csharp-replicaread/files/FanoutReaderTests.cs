namespace ReplicaRead;

/// <summary>
/// Scripted fake transport: every ReadAsync hands back one externally
/// controlled task. The tests decide exactly when and how each replica
/// answers — there are no timers and no sleeps anywhere in this suite,
/// so every run schedules identically.
/// </summary>
internal sealed class ScriptedReplica : IReplica
{
    private readonly TaskCompletionSource<string> _tcs =
        new(TaskCreationOptions.RunContinuationsAsynchronously);
    private int _calls;

    public ScriptedReplica(string name) => Name = name;

    public string Name { get; }
    public int Calls => Volatile.Read(ref _calls);
    public volatile bool SawCancel;
    public string? LastKey;

    public Task<string> ReadAsync(string key, CancellationToken ct)
    {
        Interlocked.Increment(ref _calls);
        LastKey = key;
        ct.Register(() =>
        {
            SawCancel = true;
            _tcs.TrySetCanceled(ct);
        });
        return _tcs.Task;
    }

    public void Complete(string value) => _tcs.SetResult(value);
    public void Fail(string message) => _tcs.SetException(new IOException(message));
}

public class ReadAllTests
{
    [Fact]
    public async Task IssuesEveryReadUpFrontBeforeAnyReplicaAnswers()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var c = new ScriptedReplica("carol");
        var reader = new FanoutReader(new IReplica[] { a, b, c });

        var task = reader.ReadAllAsync("limits/max-widgets");

        // all three must be in flight before a single answer arrives
        Assert.Equal(1, a.Calls);
        Assert.Equal(1, b.Calls);
        Assert.Equal(1, c.Calls);
        Assert.Equal("limits/max-widgets", a.LastKey);
        Assert.False(task.IsCompleted);

        a.Complete("40");
        b.Complete("40");
        c.Complete("40");
        Assert.Equal(new[] { "40", "40", "40" }, await task);
    }

    [Fact]
    public async Task ResultsComeBackInReplicaOrderEvenWhenAnswersArriveReversed()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var c = new ScriptedReplica("carol");
        var reader = new FanoutReader(new IReplica[] { a, b, c });

        var task = reader.ReadAllAsync("banner/motd");
        c.Complete("from-carol");
        b.Complete("from-bravo");
        a.Complete("from-alpha");

        Assert.Equal(new[] { "from-alpha", "from-bravo", "from-carol" }, await task);
        Assert.Equal(1, a.Calls);
        Assert.Equal(1, b.Calls);
        Assert.Equal(1, c.Calls);
    }

    [Fact]
    public async Task OneFailingReplicaFaultsTheWholeGather()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var reader = new FanoutReader(new IReplica[] { a, b });

        var task = reader.ReadAllAsync("limits/max-widgets");
        a.Complete("40");
        b.Fail("bravo: connection reset");

        var ex = await Assert.ThrowsAsync<IOException>(() => task);
        Assert.Equal("bravo: connection reset", ex.Message);
    }

    [Fact]
    public async Task CallerCancellationReachesEveryReplicaAndCancelsTheGather()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var c = new ScriptedReplica("carol");
        var reader = new FanoutReader(new IReplica[] { a, b, c });
        using var cts = new CancellationTokenSource();

        var task = reader.ReadAllAsync("banner/motd", cts.Token);
        Assert.Equal(1, c.Calls);

        cts.Cancel();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => task);
        Assert.True(a.SawCancel);
        Assert.True(b.SawCancel);
        Assert.True(c.SawCancel);
    }
}

public class ReadFastestTests
{
    [Fact]
    public async Task FirstSuccessfulAnswerWins()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var c = new ScriptedReplica("carol");
        var reader = new FanoutReader(new IReplica[] { a, b, c });

        var task = reader.ReadFastestAsync("features/beta");
        Assert.Equal(1, a.Calls);
        Assert.Equal(1, b.Calls);
        Assert.Equal(1, c.Calls);
        Assert.False(task.IsCompleted);

        b.Complete("v42");

        Assert.Equal(new ReplicaAnswer("bravo", "v42"), await task);
    }

    [Fact]
    public async Task LosersAreCancelledBeforeTheAnswerIsReturned()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var c = new ScriptedReplica("carol");
        var reader = new FanoutReader(new IReplica[] { a, b, c });

        var task = reader.ReadFastestAsync("features/beta");
        b.Complete("v42");
        await task;

        // by the time the caller has the answer the stragglers must already
        // have observed cancellation — no orphaned reads left running
        Assert.True(a.SawCancel);
        Assert.True(c.SawCancel);
        Assert.Equal(1, a.Calls);
        Assert.Equal(1, c.Calls);
    }

    [Fact]
    public async Task WinnerCancellationMustNotComeFromTheCallersToken()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var reader = new FanoutReader(new IReplica[] { a, b });
        using var cts = new CancellationTokenSource();

        var task = reader.ReadFastestAsync("features/beta", cts.Token);
        a.Complete("v7");
        Assert.Equal(new ReplicaAnswer("alpha", "v7"), await task);

        Assert.True(b.SawCancel);          // straggler cancelled internally...
        Assert.False(cts.IsCancellationRequested);   // ...caller's token untouched
    }

    [Fact]
    public async Task FaultedReplicasAreSkippedNotReturned()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var c = new ScriptedReplica("carol");
        var reader = new FanoutReader(new IReplica[] { a, b, c });

        var task = reader.ReadFastestAsync("features/beta");
        a.Fail("alpha: connection reset");
        Assert.False(task.IsCompleted);    // one failure is not an answer

        c.Complete("v9");
        Assert.Equal(new ReplicaAnswer("carol", "v9"), await task);
    }

    [Fact]
    public async Task LastRemainingReplicaCanStillWinAfterEveryoneElseFailed()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var c = new ScriptedReplica("carol");
        var reader = new FanoutReader(new IReplica[] { a, b, c });

        var task = reader.ReadFastestAsync("features/beta");
        a.Fail("alpha: connection reset");
        c.Fail("carol: 503");
        b.Complete("v11");

        Assert.Equal(new ReplicaAnswer("bravo", "v11"), await task);
    }

    [Fact]
    public async Task AllReplicasFailing_ReportsHowManyAndWhichKey()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var c = new ScriptedReplica("carol");
        var reader = new FanoutReader(new IReplica[] { a, b, c });

        var task = reader.ReadFastestAsync("features/beta");
        a.Fail("alpha: connection reset");
        b.Fail("bravo: 503");
        c.Fail("carol: timeout budget spent");

        var ex = await Assert.ThrowsAsync<InvalidOperationException>(() => task);
        Assert.Equal("all 3 replicas failed for key 'features/beta'", ex.Message);
    }

    [Fact]
    public async Task CallerCancellationWhileEveryReplicaIsPending_Cancels()
    {
        var a = new ScriptedReplica("alpha");
        var b = new ScriptedReplica("bravo");
        var reader = new FanoutReader(new IReplica[] { a, b });
        using var cts = new CancellationTokenSource();

        var task = reader.ReadFastestAsync("features/beta", cts.Token);
        cts.Cancel();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => task);
        Assert.True(a.SawCancel);
        Assert.True(b.SawCancel);
    }
}

public class ConstructionTests
{
    [Fact]
    public void EmptyReplicaSetIsRejectedUpFront()
    {
        Assert.Throws<ArgumentException>(() => new FanoutReader(Array.Empty<IReplica>()));
    }
}
