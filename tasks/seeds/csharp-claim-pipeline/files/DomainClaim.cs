namespace ClaimPipeline;

public enum ClaimStatus
{
    Draft,
    ReadyForSubmission,
    Submitted,
    Closed,
}

public sealed class InvalidClaimTransitionException(string message) : Exception(message);

/// <summary>The claim aggregate and its state transition.</summary>
public sealed class Claim
{
    public Claim(string id, ClaimStatus status, int version, string? submittedBy = null,
        DateTimeOffset? submittedAt = null)
    {
        Id = id;
        Status = status;
        Version = version;
        SubmittedBy = submittedBy;
        SubmittedAt = submittedAt;
    }

    public string Id { get; }
    public ClaimStatus Status { get; private set; }
    public int Version { get; private set; }
    public string? SubmittedBy { get; private set; }
    public DateTimeOffset? SubmittedAt { get; private set; }

    public void Submit(string actor, DateTimeOffset at)
    {
        int nextVersion = checked(Version + 1);
        Status = ClaimStatus.Submitted;
        SubmittedBy = actor;
        SubmittedAt = at;
        Version = nextVersion;
    }
}
