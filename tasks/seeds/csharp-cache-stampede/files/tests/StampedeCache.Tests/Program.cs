using StampedeCache;

var tests = new (string Name, Func<Task> Body)[]
{
    ("same-key misses share one load", SameKeyMissesShareOneLoad),
    ("different keys load in parallel", DifferentKeysLoadInParallel),
    ("values remain cached until expiry", ValuesRemainCachedUntilExpiry),
    ("factory failures fan out and can be retried", FactoryFailuresFanOutAndCanBeRetried),
    ("factory cancellation fans out and can be retried", FactoryCancellationFansOutAndCanBeRetried)
};

var failures = new List<string>();
foreach (var (name, body) in tests)
{
    try
    {
        await body();
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

static async Task SameKeyMissesShareOneLoad()
{
    var cache = NewCache();
    var release = NewCompletion<string>();
    var calls = 0;

    Task<string> Load(CancellationToken _)
    {
        Interlocked.Increment(ref calls);
        return release.Task;
    }

    var requests = Enumerable.Range(0, 12)
        .Select(_ => cache.GetOrCreateAsync("shared", Load, TimeSpan.FromMinutes(5)))
        .ToArray();

    var callsBeforeRelease = Volatile.Read(ref calls);
    release.SetResult("one-result");
    var results = await Task.WhenAll(requests);

    Equal(1, callsBeforeRelease, "concurrent misses for one key must invoke one factory");
    True(results.All(value => value == "one-result"), "all callers must receive the shared result");
}

static async Task DifferentKeysLoadInParallel()
{
    var cache = NewCache();
    var alphaRelease = NewCompletion<string>();
    var betaRelease = NewCompletion<string>();
    var alphaEntered = 0;
    var betaEntered = 0;

    Task<string> LoadAlpha(CancellationToken _)
    {
        Interlocked.Exchange(ref alphaEntered, 1);
        return alphaRelease.Task;
    }

    Task<string> LoadBeta(CancellationToken _)
    {
        Interlocked.Exchange(ref betaEntered, 1);
        return betaRelease.Task;
    }

    var alpha = cache.GetOrCreateAsync("alpha", LoadAlpha, TimeSpan.FromMinutes(5));
    var beta = cache.GetOrCreateAsync("beta", LoadBeta, TimeSpan.FromMinutes(5));
    var bothEnteredBeforeRelease =
        Volatile.Read(ref alphaEntered) == 1 && Volatile.Read(ref betaEntered) == 1;

    alphaRelease.SetResult("A");
    betaRelease.SetResult("B");
    await Task.WhenAll(alpha, beta);

    True(bothEnteredBeforeRelease, "a load for one key must not serialize a different key");
    Equal("A", await alpha, "alpha result");
    Equal("B", await beta, "beta result");
}

static async Task ValuesRemainCachedUntilExpiry()
{
    var clock = new ManualClock(new DateTimeOffset(2035, 1, 2, 3, 4, 5, TimeSpan.Zero));
    var cache = NewCache(clock);
    var calls = 0;

    Task<string> InitialLoad(CancellationToken _)
    {
        var current = Interlocked.Increment(ref calls);
        return Task.FromResult($"value-{current}");
    }

    var first = await cache.GetOrCreateAsync("expiring", InitialLoad, TimeSpan.FromMinutes(1));
    clock.Advance(TimeSpan.FromSeconds(59));
    var beforeExpiry = await cache.GetOrCreateAsync("expiring", InitialLoad, TimeSpan.FromMinutes(1));

    Equal("value-1", first, "initial value");
    Equal("value-1", beforeExpiry, "the unexpired entry must be reused");
    Equal(1, Volatile.Read(ref calls), "an unexpired hit must not invoke the factory");

    clock.Advance(TimeSpan.FromSeconds(1));
    var refreshRelease = NewCompletion<string>();

    Task<string> Refresh(CancellationToken _)
    {
        Interlocked.Increment(ref calls);
        return refreshRelease.Task;
    }

    var refreshes = Enumerable.Range(0, 8)
        .Select(_ => cache.GetOrCreateAsync("expiring", Refresh, TimeSpan.FromMinutes(1)))
        .ToArray();

    var callsBeforeRelease = Volatile.Read(ref calls);
    refreshRelease.SetResult("value-2");
    var refreshedValues = await Task.WhenAll(refreshes);

    Equal(2, callsBeforeRelease, "exact expiry must trigger one coalesced refresh");
    True(refreshedValues.All(value => value == "value-2"), "refresh callers must share the new value");
    Equal(
        "value-2",
        await cache.GetOrCreateAsync("expiring", InitialLoad, TimeSpan.FromMinutes(1)),
        "the refreshed value must be cached");
    Equal(2, Volatile.Read(ref calls), "reading the refreshed entry must be a cache hit");
}

static async Task FactoryFailuresFanOutAndCanBeRetried()
{
    var cache = NewCache();
    var release = NewCompletion<string>();
    var expected = new LoadFailedException("backend unavailable");
    var calls = 0;

    Task<string> Fail(CancellationToken _)
    {
        Interlocked.Increment(ref calls);
        return release.Task;
    }

    var requests = Enumerable.Range(0, 7)
        .Select(_ => cache.GetOrCreateAsync("fault", Fail, TimeSpan.FromMinutes(1)))
        .ToArray();

    var callsBeforeRelease = Volatile.Read(ref calls);
    release.SetException(expected);
    var failures = await Task.WhenAll(requests.Select(CaptureException));

    Equal(1, callsBeforeRelease, "a failing shared load must still invoke one factory");
    True(
        failures.All(exception => ReferenceEquals(expected, exception)),
        "every coalesced caller must observe the shared failure");

    var retryCalls = 0;
    var recovered = await cache.GetOrCreateAsync(
        "fault",
        _ =>
        {
            Interlocked.Increment(ref retryCalls);
            return Task.FromResult("recovered");
        },
        TimeSpan.FromMinutes(1));

    Equal("recovered", recovered, "a request after failure must be allowed to retry");
    Equal(1, retryCalls, "the failed in-flight entry must be removed");
}

static async Task FactoryCancellationFansOutAndCanBeRetried()
{
    var cache = NewCache();
    using var winnerCancellation = new CancellationTokenSource();
    using var followerCancellation = new CancellationTokenSource();
    var release = NewCompletion<string>();
    var calls = 0;
    var factoryToken = CancellationToken.None;
    var factoryReceivedItsRequestToken = 1;

    Task<string> CancelledLoad(
        CancellationToken token,
        CancellationToken expectedToken)
    {
        Interlocked.Increment(ref calls);
        if (token != expectedToken)
        {
            Interlocked.Exchange(ref factoryReceivedItsRequestToken, 0);
        }

        factoryToken = token;
        return release.Task;
    }

    var requests = new List<Task<string>>
    {
        cache.GetOrCreateAsync(
            "cancelled",
            token => CancelledLoad(token, winnerCancellation.Token),
            TimeSpan.FromMinutes(1),
            winnerCancellation.Token)
    };
    requests.AddRange(Enumerable.Range(0, 5).Select(_ => cache.GetOrCreateAsync(
        "cancelled",
        token => CancelledLoad(token, followerCancellation.Token),
        TimeSpan.FromMinutes(1),
        followerCancellation.Token)));

    var callsBeforeCancellation = Volatile.Read(ref calls);
    winnerCancellation.Cancel();
    followerCancellation.Cancel();
    release.SetCanceled(factoryToken);
    var failures = await Task.WhenAll(requests.Select(CaptureException));

    Equal(1, callsBeforeCancellation, "a cancelled shared load must still invoke one factory");
    Equal(
        1,
        Volatile.Read(ref factoryReceivedItsRequestToken),
        "the winning request's token must be passed to its factory");
    True(
        failures.All(exception =>
            exception is OperationCanceledException cancelled &&
            cancelled.CancellationToken == factoryToken),
        "every coalesced caller must observe cancellation from the shared load");

    var retryCalls = 0;
    var recovered = await cache.GetOrCreateAsync(
        "cancelled",
        _ =>
        {
            Interlocked.Increment(ref retryCalls);
            return Task.FromResult("after-cancellation");
        },
        TimeSpan.FromMinutes(1));

    Equal("after-cancellation", recovered, "a request after cancellation must retry");
    Equal(1, retryCalls, "the cancelled in-flight entry must be removed");
}

static AsyncExpiringCache<string, string> NewCache(ICacheClock? clock = null) =>
    new(clock, StringComparer.Ordinal);

static TaskCompletionSource<T> NewCompletion<T>() =>
    new(TaskCreationOptions.RunContinuationsAsynchronously);

static async Task<Exception> CaptureException(Task task)
{
    try
    {
        await task;
    }
    catch (Exception exception)
    {
        return exception;
    }

    throw new TestFailureException("expected the operation to fail");
}

static void Equal<T>(T expected, T actual, string message)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new TestFailureException($"{message}: expected '{expected}', got '{actual}'");
    }
}

static void True(bool condition, string message)
{
    if (!condition)
    {
        throw new TestFailureException(message);
    }
}

sealed class ManualClock(DateTimeOffset utcNow) : ICacheClock
{
    public DateTimeOffset UtcNow { get; private set; } = utcNow;

    public void Advance(TimeSpan amount)
    {
        UtcNow = UtcNow.Add(amount);
    }
}

sealed class LoadFailedException(string message) : Exception(message);

sealed class TestFailureException(string message) : Exception(message);
