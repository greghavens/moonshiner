namespace ClaimPipeline;

/// <summary>Domain command handler shared by mediator and non-HTTP callers.</summary>
public sealed class SubmitClaimHandler
{
    private readonly IClaimStore _store;
    private readonly IClaimEventSink _events;
    private readonly IClock _clock;

    public SubmitClaimHandler(IClaimStore store, IClaimEventSink events, IClock clock)
    {
        _store = store;
        _events = events;
        _clock = clock;
    }

    public int Calls { get; private set; }

    public async Task<SubmitClaimResult> HandleAsync(
        SubmitClaimCommand command,
        CurrentUser user,
        CancellationToken cancellationToken = default)
    {
        Calls++;
        var claim = await _store.LoadAsync(command.ClaimId, cancellationToken);
        var submittedAt = _clock.UtcNow;
        claim.Submit(user.Id, submittedAt);
        await _store.SaveAsync(claim, cancellationToken);
        await _events.PublishAsync(
            new ClaimSubmittedEvent(claim.Id, claim.Version, user.Id, submittedAt),
            cancellationToken);
        return new SubmitClaimResult(
            claim.Id, claim.Status, claim.Version, user.Id, submittedAt);
    }
}
