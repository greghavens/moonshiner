namespace ClockSkewIncident;

public sealed record RemoteLease(
    string LeaseId,
    DateTimeOffset IssuedAtUtc,
    DateTimeOffset ExpiresAtUtc,
    TimeSpan MaximumLocalAge);

public sealed record TrackedLease(RemoteLease Lease, long ReceivedTimestamp);

public enum LeaseExpiryStatus
{
    Active,
    RemoteDeadlineElapsed,
    LocalAgeElapsed
}

public sealed record ExpiryAudit(
    string LeaseId,
    DateTimeOffset RemoteIssuedAtUtc,
    DateTimeOffset RemoteExpiresAtUtc,
    DateTimeOffset EvaluatedAtLocalUtc,
    TimeSpan LocalElapsed);

public sealed record ExpiryDecision(LeaseExpiryStatus Status, ExpiryAudit Audit);
