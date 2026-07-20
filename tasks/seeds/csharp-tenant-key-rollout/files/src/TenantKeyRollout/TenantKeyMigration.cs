namespace TenantKeyRollout;

public sealed class TenantKeyMigration
{
    private readonly ITenantRecordStore _store;

    public TenantKeyMigration(ITenantRecordStore store)
    {
        _store = store ?? throw new ArgumentNullException(nameof(store));
    }

    public BackfillBatchResult BackfillNextBatch(int batchSize)
    {
        if (batchSize <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(batchSize));
        }

        var records = _store.ReadMissingTenantKeys(batchSize);
        var updated = 0;
        var alreadySet = 0;
        var conflicts = new List<long>();

        foreach (var record in records)
        {
            var tenantKey = TenantScopedKey.Create(record.TenantId, record.ExternalKey);
            switch (_store.TrySetTenantKey(record.Id, tenantKey))
            {
                case TenantKeyUpdateStatus.Updated:
                    updated++;
                    break;
                case TenantKeyUpdateStatus.AlreadySet:
                case TenantKeyUpdateStatus.Missing:
                    alreadySet++;
                    break;
                case TenantKeyUpdateStatus.UniqueConflict:
                    conflicts.Add(record.Id);
                    break;
                default:
                    throw new InvalidOperationException("The record store returned an unknown update status.");
            }
        }

        return new BackfillBatchResult(
            records.Count,
            updated,
            alreadySet,
            conflicts.AsReadOnly());
    }

    public void CompleteMigration()
    {
        var readiness = _store.InspectMigrationReadiness();
        if (!readiness.IsReady)
        {
            throw new TenantKeyMigrationNotReadyException(readiness.MissingTenantKeyCount);
        }

        _store.EnableLegacyWriterCompatibility();
        _store.ActivateFinalTenantKeyConstraints();
    }
}
