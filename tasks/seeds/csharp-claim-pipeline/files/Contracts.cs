namespace ClaimPipeline;

public sealed record SubmitClaimCommand(
    string ClaimId,
    string IdempotencyKey,
    IReadOnlyList<string> DocumentIds);

public sealed record CurrentUser(string Id, IReadOnlySet<string> Permissions);

public sealed record SubmitClaimResult(
    string ClaimId,
    ClaimStatus Status,
    int Version,
    string SubmittedBy,
    DateTimeOffset SubmittedAt);

public sealed record ClaimSubmittedEvent(
    string ClaimId,
    int Version,
    string Actor,
    DateTimeOffset At);

public sealed record ApiResponse(int Status, string Body);

public sealed class ClaimValidationException(string message) : Exception(message);
public sealed class IdempotencyConflictException(string message) : Exception(message);
