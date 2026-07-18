namespace ClaimPipeline;

public sealed class ClaimAuthorizer
{
    public void DemandSubmit(CurrentUser user)
    {
        if (!user.Permissions.Contains("claims:submit"))
            throw new UnauthorizedAccessException("claims:submit required");
    }
}

public sealed class ClaimValidator
{
    public void Validate(SubmitClaimCommand command)
    {
        if (string.IsNullOrWhiteSpace(command.ClaimId))
            throw new ClaimValidationException("claim id is required");
        if (string.IsNullOrWhiteSpace(command.IdempotencyKey))
            throw new ClaimValidationException("idempotency key is required");
        if (command.DocumentIds.Count == 0)
            throw new ClaimValidationException("at least one document is required");
    }
}

public sealed class ClaimIdempotencyStore
{
    private sealed record Entry(string Fingerprint, SubmitClaimResult Result);
    private readonly Dictionary<string, Entry> _entries = new(StringComparer.Ordinal);

    public bool TryReplay(CurrentUser user, SubmitClaimCommand command, out SubmitClaimResult? result)
    {
        var key = user.Id + "\n" + command.IdempotencyKey;
        if (!_entries.TryGetValue(key, out var entry))
        {
            result = null;
            return false;
        }
        if (!string.Equals(entry.Fingerprint, Fingerprint(command), StringComparison.Ordinal))
            throw new IdempotencyConflictException("idempotency key reused with different request");
        result = entry.Result;
        return true;
    }

    public void Store(CurrentUser user, SubmitClaimCommand command, SubmitClaimResult result) =>
        _entries[user.Id + "\n" + command.IdempotencyKey] = new(Fingerprint(command), result);

    private static string Fingerprint(SubmitClaimCommand command) =>
        command.ClaimId + "\n" + string.Join("\n", command.DocumentIds);
}

/// <summary>Explicit authorization -> validation -> idempotency -> handler pipeline.</summary>
public sealed class ClaimMediator
{
    private readonly ClaimAuthorizer _authorizer;
    private readonly ClaimValidator _validator;
    private readonly ClaimIdempotencyStore _idempotency;
    private readonly SubmitClaimHandler _handler;

    public ClaimMediator(ClaimAuthorizer authorizer, ClaimValidator validator,
        ClaimIdempotencyStore idempotency, SubmitClaimHandler handler)
    {
        _authorizer = authorizer;
        _validator = validator;
        _idempotency = idempotency;
        _handler = handler;
    }

    public async Task<SubmitClaimResult> SendAsync(
        SubmitClaimCommand command,
        CurrentUser user,
        CancellationToken cancellationToken = default)
    {
        _authorizer.DemandSubmit(user);
        _validator.Validate(command);
        if (_idempotency.TryReplay(user, command, out var replay))
            return replay!;
        var result = await _handler.HandleAsync(command, user, cancellationToken);
        _idempotency.Store(user, command, result);
        return result;
    }
}
