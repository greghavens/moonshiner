using System.Diagnostics;

namespace TokenRefreshStorm;

public sealed class TokenProvider
{
    private readonly ITokenEndpoint _endpoint;
    private readonly IClock _clock;
    private readonly IRefreshTelemetry _telemetry;
    private readonly TimeSpan _refreshBeforeExpiry;
    private AccessToken? _cachedToken;
    private long _nextRefreshId;

    public TokenProvider(
        ITokenEndpoint endpoint,
        IClock clock,
        IRefreshTelemetry? telemetry = null,
        AccessToken? initialToken = null,
        TimeSpan? refreshBeforeExpiry = null)
    {
        _endpoint = endpoint ?? throw new ArgumentNullException(nameof(endpoint));
        _clock = clock ?? throw new ArgumentNullException(nameof(clock));
        _telemetry = telemetry ?? NullRefreshTelemetry.Instance;
        _cachedToken = initialToken;
        _refreshBeforeExpiry = refreshBeforeExpiry ?? TimeSpan.FromSeconds(30);
    }

    public async Task<string> GetAccessTokenAsync(CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();

        var cachedToken = Volatile.Read(ref _cachedToken);
        if (IsUsable(cachedToken))
        {
            return cachedToken!.Value;
        }

        // Refresh after the cached token crosses the proactive refresh window.
        var refreshId = Interlocked.Increment(ref _nextRefreshId);
        var startedAt = Stopwatch.GetTimestamp();
        RecordSafely(new RefreshEvent(
            RefreshEventKind.Started,
            refreshId,
            Participants: 1,
            Duration: TimeSpan.Zero));

        try
        {
            var refreshed = await _endpoint.RefreshAsync(cancellationToken).ConfigureAwait(false);
            Volatile.Write(ref _cachedToken, refreshed);
            RecordSafely(new RefreshEvent(
                RefreshEventKind.Succeeded,
                refreshId,
                Participants: 1,
                Duration: Stopwatch.GetElapsedTime(startedAt)));
            return refreshed.Value;
        }
        catch (OperationCanceledException)
        {
            RecordSafely(new RefreshEvent(
                RefreshEventKind.Canceled,
                refreshId,
                Participants: 1,
                Duration: Stopwatch.GetElapsedTime(startedAt)));
            throw;
        }
        catch (Exception exception)
        {
            RecordSafely(new RefreshEvent(
                RefreshEventKind.Failed,
                refreshId,
                Participants: 1,
                Duration: Stopwatch.GetElapsedTime(startedAt),
                FailureType: exception.GetType().FullName));
            throw;
        }
    }

    private bool IsUsable(AccessToken? token) =>
        token is not null && token.ExpiresAtUtc > _clock.UtcNow + _refreshBeforeExpiry;

    private void RecordSafely(RefreshEvent refreshEvent)
    {
        try
        {
            _telemetry.Record(refreshEvent);
        }
        catch
        {
            // Telemetry must not turn a successful token lookup into a request failure.
        }
    }
}
