namespace TenantKeyRollout;

public sealed record TenantRecord(
    long Id,
    string TenantId,
    string ExternalKey,
    string? TenantKey);
