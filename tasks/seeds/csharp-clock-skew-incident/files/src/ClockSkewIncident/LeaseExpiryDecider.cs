namespace ClockSkewIncident;

public sealed class LeaseExpiryDecider
{
    private readonly ISystemClock _clock;
    private readonly IRemoteClockSkew _remoteClockSkew;

    public LeaseExpiryDecider(ISystemClock clock, IRemoteClockSkew remoteClockSkew)
    {
        _clock = clock ?? throw new ArgumentNullException(nameof(clock));
        _remoteClockSkew = remoteClockSkew ?? throw new ArgumentNullException(nameof(remoteClockSkew));
    }

    public ExpiryDecision Evaluate(TrackedLease tracked)
    {
        ArgumentNullException.ThrowIfNull(tracked);

        var localNow = _clock.UtcNow;
        var localElapsed = _clock.GetElapsedTime(tracked.ReceivedTimestamp);

        // Remote expiry is authored in the producer's wall-clock domain.
        var expiredByRemoteDeadline = localNow >= tracked.Lease.ExpiresAtUtc;

        var status = expiredByRemoteDeadline
            ? LeaseExpiryStatus.RemoteDeadlineElapsed
            : localElapsed >= tracked.Lease.MaximumLocalAge
                ? LeaseExpiryStatus.LocalAgeElapsed
                : LeaseExpiryStatus.Active;

        return new ExpiryDecision(
            status,
            new ExpiryAudit(
                tracked.Lease.LeaseId,
                tracked.Lease.IssuedAtUtc,
                tracked.Lease.ExpiresAtUtc,
                localNow,
                localElapsed));
    }
}
