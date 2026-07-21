namespace MobileOfflineOutbox;

public enum OutboxEntryState
{
    Pending,
    Conflict
}

public sealed record OutboxConflict(int ServerVersion, string ServerBody);

public sealed record OutboxEntry(
    Guid OperationId,
    long Sequence,
    string DocumentId,
    int BaseVersion,
    string Body,
    OutboxEntryState State,
    OutboxConflict? Conflict);

public enum PushOutcome
{
    Applied,
    AlreadyApplied,
    Conflict,
    RetryLater
}

public sealed record PushResult(
    PushOutcome Outcome,
    int? ServerVersion = null,
    string? ServerBody = null)
{
    public static PushResult Applied { get; } = new(PushOutcome.Applied);

    public static PushResult AlreadyApplied { get; } = new(PushOutcome.AlreadyApplied);

    public static PushResult RetryLater { get; } = new(PushOutcome.RetryLater);

    public static PushResult Conflicted(int serverVersion, string serverBody)
    {
        ArgumentOutOfRangeException.ThrowIfNegative(serverVersion);
        ArgumentNullException.ThrowIfNull(serverBody);
        return new PushResult(PushOutcome.Conflict, serverVersion, serverBody);
    }
}

public interface IEditTransport
{
    Task<PushResult> PushAsync(
        OutboxEntry entry,
        CancellationToken cancellationToken);
}

public enum ReconcileStopReason
{
    Drained,
    Conflict,
    RetryLater
}

public sealed record ReconcileResult(
    ReconcileStopReason StopReason,
    int AcknowledgedCount);
