namespace TenantKeyRollout;

public enum TenantKeyUpdateStatus
{
    Updated,
    AlreadySet,
    UniqueConflict,
    Missing
}

public sealed record MigrationReadiness(int MissingTenantKeyCount)
{
    public bool IsReady => MissingTenantKeyCount == 0;
}

public sealed record BackfillBatchResult(
    int Scanned,
    int Updated,
    int AlreadySet,
    IReadOnlyList<long> ConflictingRecordIds)
{
    public bool HasConflicts => ConflictingRecordIds.Count != 0;
}

public interface ITenantRecordStore
{
    IReadOnlyList<TenantRecord> ReadMissingTenantKeys(int maximumCount);

    TenantKeyUpdateStatus TrySetTenantKey(long recordId, string tenantKey);

    MigrationReadiness InspectMigrationReadiness();

    void EnableLegacyWriterCompatibility();

    void ActivateFinalTenantKeyConstraints();
}

public sealed class TenantKeyMigrationNotReadyException : InvalidOperationException
{
    public TenantKeyMigrationNotReadyException(int missingCount)
        : base($"Tenant-key migration still has {missingCount} record(s) without a key.")
    {
        MissingCount = missingCount;
    }

    public int MissingCount { get; }
}

public sealed class TenantKeyUniqueConstraintException : InvalidOperationException
{
    public TenantKeyUniqueConstraintException(string tenantKey)
        : base($"Tenant key '{tenantKey}' is already assigned to another record.")
    {
        TenantKey = tenantKey;
    }

    public string TenantKey { get; }
}

public sealed class UnsafeTenantKeyConstraintActivationException : InvalidOperationException
{
    public UnsafeTenantKeyConstraintActivationException(int missingCount)
        : base($"Cannot activate the required tenant-key constraint with {missingCount} missing key(s).")
    {
        MissingCount = missingCount;
    }

    public int MissingCount { get; }
}
