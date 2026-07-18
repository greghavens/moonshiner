namespace ClaimPipeline;

public interface IClaimStore
{
    Task<Claim> LoadAsync(string claimId, CancellationToken cancellationToken);
    Task SaveAsync(Claim claim, CancellationToken cancellationToken);
}

public interface IClaimEventSink
{
    Task PublishAsync(ClaimSubmittedEvent claimEvent, CancellationToken cancellationToken);
}

public interface IClock
{
    DateTimeOffset UtcNow { get; }
}
