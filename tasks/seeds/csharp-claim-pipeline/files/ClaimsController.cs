namespace ClaimPipeline;

/// <summary>HTTP-shaped adapter; all policy behaviors remain in the mediator pipeline.</summary>
public sealed class ClaimsController
{
    private readonly ClaimMediator _mediator;

    public ClaimsController(ClaimMediator mediator) => _mediator = mediator;

    public async Task<ApiResponse> SubmitAsync(
        CurrentUser user,
        string claimId,
        string idempotencyKey,
        IReadOnlyList<string> documentIds,
        CancellationToken cancellationToken = default)
    {
        try
        {
            var result = await _mediator.SendAsync(
                new SubmitClaimCommand(claimId, idempotencyKey, documentIds),
                user,
                cancellationToken);
            return new ApiResponse(200,
                $"submitted {result.ClaimId} v{result.Version} by {result.SubmittedBy} at " +
                result.SubmittedAt.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'"));
        }
        catch (UnauthorizedAccessException error)
        {
            return new ApiResponse(403, error.Message);
        }
        catch (ClaimValidationException error)
        {
            return new ApiResponse(400, error.Message);
        }
        catch (InvalidClaimTransitionException error)
        {
            return new ApiResponse(409, error.Message);
        }
        catch (IdempotencyConflictException error)
        {
            return new ApiResponse(409, error.Message);
        }
    }
}
