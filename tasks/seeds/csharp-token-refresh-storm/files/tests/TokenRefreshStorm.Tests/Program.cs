using System.Collections.Concurrent;
using TokenRefreshStorm;

internal static class Program
{
    private static readonly DateTimeOffset Now = new(2025, 1, 2, 3, 4, 5, TimeSpan.Zero);

    private static async Task<int> Main()
    {
        var tests = new (string Name, Func<Task> Run)[]
        {
            ("valid token uses the fast path", ValidTokenUsesFastPath),
            ("expired request wave is coalesced and correlated", ExpiredWaveIsCoalesced),
            ("refresh failure is shared and can be retried", FailureIsSharedAndRetryable),
            ("one canceled waiter does not cancel the refresh", WaiterCancellationIsIsolated),
            ("the starter can cancel only its own wait", StarterCancellationIsIsolated),
            ("endpoint cancellation is shared and can be retried", EndpointCancellationIsSharedAndRetryable)
        };

        foreach (var test in tests)
        {
            try
            {
                await test.Run();
                Console.WriteLine($"PASS {test.Name}");
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine($"FAIL {test.Name}");
                Console.Error.WriteLine(exception);
                return 1;
            }
        }

        return 0;
    }

    private static async Task ValidTokenUsesFastPath()
    {
        var endpoint = new ControlledEndpoint((_, _) =>
            Task.FromException<AccessToken>(new InvalidOperationException("refresh was not expected")));
        var telemetry = new RecordingTelemetry();
        var provider = CreateProvider(
            endpoint,
            telemetry,
            new AccessToken("still-valid", Now.AddMinutes(5)));

        var value = await provider.GetAccessTokenAsync();

        Equal("still-valid", value, "The cached token should be returned.");
        Equal(0, endpoint.CallCount, "The valid-token path must not call the endpoint.");
        Equal(0, telemetry.Events.Count, "The valid-token path must not emit refresh events.");
    }

    private static async Task ExpiredWaveIsCoalesced()
    {
        var release = NewCompletionSource<AccessToken>();
        var endpoint = new ControlledEndpoint((_, _) => release.Task);
        var telemetry = new RecordingTelemetry();
        var provider = CreateProvider(endpoint, telemetry, ExpiredToken());

        var requests = Enumerable.Range(0, 24)
            .Select(_ => provider.GetAccessTokenAsync())
            .ToArray();

        await WaitUntil(() => endpoint.CallCount >= 1, "The refresh did not start.");
        Equal(1, endpoint.CallCount, "A stale-token request wave must make one upstream call.");

        release.SetResult(new AccessToken("wave-token", Now.AddMinutes(10)));
        var values = await Task.WhenAll(requests);

        True(values.All(value => value == "wave-token"), "Every participant must receive the refreshed token.");
        Equal(1, endpoint.CallCount, "Followers must join instead of refreshing.");

        var events = telemetry.Events.ToArray();
        var started = Single(events, RefreshEventKind.Started);
        var succeeded = Single(events, RefreshEventKind.Succeeded);
        Equal(started.RefreshId, succeeded.RefreshId, "Terminal telemetry must correlate to the start.");
        Equal(1, started.Participants, "Started telemetry must count the flight owner.");
        Equal(TimeSpan.Zero, started.Duration, "Started telemetry must begin at zero duration.");
        var joined = events.Where(item => item.Kind == RefreshEventKind.Joined).ToArray();
        Equal(23, joined.Length, "Each follower needs a Joined event.");
        True(joined.All(item => item.RefreshId == started.RefreshId),
            "Joined events must use the shared refresh id.");
        True(joined.All(item => item.Duration >= TimeSpan.Zero),
            "Joined telemetry must include time spent in the flight.");
        True(joined.Select(item => item.Participants).OrderBy(item => item)
            .SequenceEqual(Enumerable.Range(2, 23)),
            "Joined telemetry must report the cumulative participant count.");
        Equal(24, succeeded.Participants, "Terminal telemetry must report the request-wave size.");
        True(succeeded.Duration >= TimeSpan.Zero, "Terminal telemetry must include a duration.");
        Equal<string?>(null, succeeded.FailureType,
            "Successful refresh telemetry must not report a failure type.");
    }

    private static async Task FailureIsSharedAndRetryable()
    {
        var failure = NewCompletionSource<AccessToken>();
        var endpoint = new ControlledEndpoint((_, _) => failure.Task);
        var telemetry = new RecordingTelemetry();
        var provider = CreateProvider(endpoint, telemetry, ExpiredToken());
        var expected = new RefreshRejectedException("identity provider rejected the refresh");

        var requests = Enumerable.Range(0, 12)
            .Select(_ => provider.GetAccessTokenAsync())
            .ToArray();

        await WaitUntil(() => endpoint.CallCount >= 1, "The failing refresh did not start.");
        Equal(1, endpoint.CallCount, "Failure participants must still share one upstream call.");
        failure.SetException(expected);

        foreach (var request in requests)
        {
            var observed = await ThrowsAsync<RefreshRejectedException>(() => request);
            True(ReferenceEquals(expected, observed), "Participants must observe the original failure instance.");
        }

        var failureEvents = telemetry.Events.ToArray();
        var failureStarted = Single(failureEvents, RefreshEventKind.Started);
        var failed = Single(failureEvents, RefreshEventKind.Failed);
        Equal(failureStarted.RefreshId, failed.RefreshId, "Failure telemetry must correlate to its start.");
        Equal(typeof(RefreshRejectedException).FullName, failed.FailureType, "Failure telemetry needs the exception type.");
        Equal(12, failed.Participants, "Failure telemetry must report all participants.");
        True(failed.Duration >= TimeSpan.Zero, "Failure telemetry must include a duration.");

        endpoint.Handler = (_, _) => Task.FromResult(new AccessToken("recovered", Now.AddMinutes(10)));
        Equal("recovered", await provider.GetAccessTokenAsync(), "A failed flight must not prevent a retry.");
        Equal(2, endpoint.CallCount, "The first request after failure should start one new refresh.");
        Equal(2, telemetry.Events.Count(item => item.Kind == RefreshEventKind.Started), "A retry needs a new Started event.");
    }

    private static async Task WaiterCancellationIsIsolated()
    {
        var release = NewCompletionSource<AccessToken>();
        var endpoint = new ControlledEndpoint((_, cancellationToken) =>
            release.Task.WaitAsync(cancellationToken));
        var telemetry = new RecordingTelemetry();
        var provider = CreateProvider(endpoint, telemetry, ExpiredToken());

        var survivor = provider.GetAccessTokenAsync();
        await WaitUntil(() => endpoint.CallCount >= 1, "The shared refresh did not start.");

        using var cancellation = new CancellationTokenSource();
        var abandoningWaiter = provider.GetAccessTokenAsync(cancellation.Token);
        cancellation.Cancel();
        var waiterCancellation = await ThrowsAsync<OperationCanceledException>(() => abandoningWaiter);
        Equal(cancellation.Token, waiterCancellation.CancellationToken,
            "The abandoning waiter must observe its own cancellation token.");

        Equal(1, endpoint.CallCount, "A canceled follower must not start another refresh.");
        True(endpoint.ObservedTokens.All(token => !token.CanBeCanceled),
            "A request cancellation token must not own the shared upstream refresh.");

        release.SetResult(new AccessToken("survivor-token", Now.AddMinutes(10)));
        Equal("survivor-token", await survivor, "Other participants must survive one waiter's cancellation.");

        var started = Single(telemetry.Events, RefreshEventKind.Started);
        var joined = Single(telemetry.Events, RefreshEventKind.Joined);
        var waiterCanceled = Single(telemetry.Events, RefreshEventKind.WaiterCanceled);
        Equal(2, joined.Participants, "The follower must increase the participant count.");
        Equal(started.RefreshId, waiterCanceled.RefreshId, "Waiter cancellation must identify the shared refresh.");
        Equal(2, waiterCanceled.Participants, "Waiter cancellation must report the flight participant count.");
        True(waiterCanceled.Duration >= TimeSpan.Zero, "Waiter cancellation must include a duration.");
        var succeeded = Single(telemetry.Events, RefreshEventKind.Succeeded);
        Equal(started.RefreshId, succeeded.RefreshId, "Success must identify the shared refresh.");
        Equal(2, succeeded.Participants, "Terminal counts include a waiter that abandoned its wait.");
        Equal(0, telemetry.Events.Count(item => item.Kind == RefreshEventKind.Canceled),
            "Canceling a waiter is not cancellation of the upstream refresh.");
    }

    private static async Task StarterCancellationIsIsolated()
    {
        var release = NewCompletionSource<AccessToken>();
        var endpoint = new ControlledEndpoint((_, cancellationToken) =>
            release.Task.WaitAsync(cancellationToken));
        var telemetry = new RecordingTelemetry();
        var provider = CreateProvider(endpoint, telemetry, ExpiredToken());

        using var cancellation = new CancellationTokenSource();
        var abandoningStarter = provider.GetAccessTokenAsync(cancellation.Token);
        await WaitUntil(() => endpoint.CallCount >= 1, "The shared refresh did not start.");
        var survivor = provider.GetAccessTokenAsync();

        cancellation.Cancel();
        var ownerCancellation = await ThrowsAsync<OperationCanceledException>(() => abandoningStarter);
        Equal(cancellation.Token, ownerCancellation.CancellationToken,
            "The flight owner must observe its own wait-cancellation token.");
        Equal(1, endpoint.CallCount, "Canceling the flight owner must not start another refresh.");
        True(endpoint.ObservedTokens.All(token => !token.CanBeCanceled),
            "The flight owner's request token must not own the upstream refresh.");

        release.SetResult(new AccessToken("starter-survivor", Now.AddMinutes(10)));
        Equal("starter-survivor", await survivor,
            "A follower must survive cancellation of the participant that started the flight.");

        var started = Single(telemetry.Events, RefreshEventKind.Started);
        var waiterCanceled = Single(telemetry.Events, RefreshEventKind.WaiterCanceled);
        var succeeded = Single(telemetry.Events, RefreshEventKind.Succeeded);
        Equal(started.RefreshId, waiterCanceled.RefreshId,
            "Owner wait cancellation must identify the shared refresh.");
        Equal(started.RefreshId, succeeded.RefreshId,
            "The surviving shared refresh must retain its correlation id.");
        Equal(2, succeeded.Participants, "Both owner and follower participated in the flight.");
        Equal(0, telemetry.Events.Count(item => item.Kind == RefreshEventKind.Canceled),
            "Canceling the owner's wait is not endpoint cancellation.");
    }

    private static async Task EndpointCancellationIsSharedAndRetryable()
    {
        var canceledRefresh = NewCompletionSource<AccessToken>();
        var endpoint = new ControlledEndpoint((_, _) => canceledRefresh.Task);
        var telemetry = new RecordingTelemetry();
        var provider = CreateProvider(endpoint, telemetry, ExpiredToken());

        var requests = Enumerable.Range(0, 9)
            .Select(_ => provider.GetAccessTokenAsync())
            .ToArray();

        await WaitUntil(() => endpoint.CallCount >= 1, "The canceling refresh did not start.");
        Equal(1, endpoint.CallCount, "Cancellation participants must share one upstream call.");
        CancellationToken endpointToken;
        using (var endpointCancellation = new CancellationTokenSource())
        {
            endpointCancellation.Cancel();
            endpointToken = endpointCancellation.Token;
            canceledRefresh.SetCanceled(endpointToken);
        }

        foreach (var request in requests)
        {
            var observed = await ThrowsAsync<OperationCanceledException>(() => request);
            Equal(endpointToken, observed.CancellationToken,
                "Every participant must observe the endpoint cancellation token.");
        }

        var cancellationEvents = telemetry.Events.ToArray();
        var cancellationStarted = Single(cancellationEvents, RefreshEventKind.Started);
        var canceled = Single(cancellationEvents, RefreshEventKind.Canceled);
        Equal(cancellationStarted.RefreshId, canceled.RefreshId,
            "Endpoint cancellation telemetry must correlate to its start.");
        Equal(9, canceled.Participants, "Cancellation telemetry must report all participants.");
        True(canceled.Duration >= TimeSpan.Zero, "Cancellation telemetry must include a duration.");
        Equal<string?>(null, canceled.FailureType, "Cancellation is not failure telemetry.");

        endpoint.Handler = (_, _) => Task.FromResult(new AccessToken("after-cancel", Now.AddMinutes(10)));
        Equal("after-cancel", await provider.GetAccessTokenAsync(), "A canceled flight must not prevent a retry.");
        Equal(2, endpoint.CallCount, "The first request after endpoint cancellation should retry.");
    }

    private static TokenProvider CreateProvider(
        ControlledEndpoint endpoint,
        RecordingTelemetry telemetry,
        AccessToken initialToken) =>
        new(endpoint, new FixedClock(Now), telemetry, initialToken, TimeSpan.FromSeconds(30));

    private static AccessToken ExpiredToken() => new("expired", Now.AddMinutes(-1));

    private static RefreshEvent Single(IEnumerable<RefreshEvent> events, RefreshEventKind kind)
    {
        var matches = events.Where(item => item.Kind == kind).ToArray();
        Equal(1, matches.Length, $"Expected exactly one {kind} event.");
        return matches[0];
    }

    private static async Task<TException> ThrowsAsync<TException>(Func<Task> action)
        where TException : Exception
    {
        try
        {
            await action();
        }
        catch (TException exception)
        {
            return exception;
        }

        throw new TestFailureException($"Expected {typeof(TException).Name}.");
    }

    private static async Task WaitUntil(Func<bool> condition, string message)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition())
        {
            try
            {
                await Task.Delay(1, timeout.Token);
            }
            catch (OperationCanceledException)
            {
                throw new TestFailureException(message);
            }
        }
    }

    private static TaskCompletionSource<T> NewCompletionSource<T>() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private static void Equal<T>(T expected, T actual, string message)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new TestFailureException($"{message} Expected: {expected}; actual: {actual}.");
        }
    }

    private static void True(bool condition, string message)
    {
        if (!condition)
        {
            throw new TestFailureException(message);
        }
    }

    private sealed class FixedClock(DateTimeOffset utcNow) : IClock
    {
        public DateTimeOffset UtcNow { get; } = utcNow;
    }

    private sealed class RecordingTelemetry : IRefreshTelemetry
    {
        public ConcurrentQueue<RefreshEvent> Events { get; } = new();

        public void Record(RefreshEvent refreshEvent) => Events.Enqueue(refreshEvent);
    }

    private sealed class ControlledEndpoint : ITokenEndpoint
    {
        private int _callCount;

        public ControlledEndpoint(Func<int, CancellationToken, Task<AccessToken>> handler)
        {
            Handler = handler;
        }

        public Func<int, CancellationToken, Task<AccessToken>> Handler { get; set; }

        public int CallCount => Volatile.Read(ref _callCount);

        public ConcurrentQueue<CancellationToken> ObservedTokens { get; } = new();

        public Task<AccessToken> RefreshAsync(CancellationToken cancellationToken)
        {
            ObservedTokens.Enqueue(cancellationToken);
            var callNumber = Interlocked.Increment(ref _callCount);
            return Handler(callNumber, cancellationToken);
        }
    }

    private sealed class RefreshRejectedException(string message) : Exception(message);

    private sealed class TestFailureException(string message) : Exception(message);
}
