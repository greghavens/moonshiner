namespace TenantKeyRollout;

public sealed class InMemoryTenantRecordStore : ITenantRecordStore
{
    private readonly object _gate = new();
    private readonly SortedDictionary<long, TenantRecord> _records = new();
    private readonly Dictionary<string, long> _tenantKeyOwners = new(StringComparer.Ordinal);
    private bool _legacyWriterCompatibilityEnabled;
    private bool _finalConstraintsActive;
    private int _constraintActivationAttempts;

    public InMemoryTenantRecordStore(IEnumerable<TenantRecord>? existingRecords = null)
    {
        if (existingRecords is null)
        {
            return;
        }

        foreach (var record in existingRecords)
        {
            AddExistingRecord(record);
        }
    }

    public bool LegacyWriterCompatibilityEnabled
    {
        get
        {
            lock (_gate)
            {
                return _legacyWriterCompatibilityEnabled;
            }
        }
    }

    public bool FinalConstraintsActive
    {
        get
        {
            lock (_gate)
            {
                return _finalConstraintsActive;
            }
        }
    }

    public int ConstraintActivationAttempts
    {
        get
        {
            lock (_gate)
            {
                return _constraintActivationAttempts;
            }
        }
    }

    public IReadOnlyList<TenantRecord> ReadMissingTenantKeys(int maximumCount)
    {
        if (maximumCount <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(maximumCount));
        }

        lock (_gate)
        {
            return _records.Values
                .Where(record => record.TenantKey is null)
                .Take(maximumCount)
                .ToArray();
        }
    }

    public TenantKeyUpdateStatus TrySetTenantKey(long recordId, string tenantKey)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(tenantKey);

        lock (_gate)
        {
            if (!_records.TryGetValue(recordId, out var record))
            {
                return TenantKeyUpdateStatus.Missing;
            }

            if (record.TenantKey is not null)
            {
                return TenantKeyUpdateStatus.AlreadySet;
            }

            if (_tenantKeyOwners.ContainsKey(tenantKey))
            {
                return TenantKeyUpdateStatus.UniqueConflict;
            }

            _records[recordId] = record with { TenantKey = tenantKey };
            _tenantKeyOwners.Add(tenantKey, recordId);
            return TenantKeyUpdateStatus.Updated;
        }
    }

    public MigrationReadiness InspectMigrationReadiness()
    {
        lock (_gate)
        {
            return new MigrationReadiness(
                _records.Values.Count(record => record.TenantKey is null));
        }
    }

    public void EnableLegacyWriterCompatibility()
    {
        lock (_gate)
        {
            _legacyWriterCompatibilityEnabled = true;
        }
    }

    public void ActivateFinalTenantKeyConstraints()
    {
        lock (_gate)
        {
            _constraintActivationAttempts++;
            var missingCount = _records.Values.Count(record => record.TenantKey is null);
            if (missingCount != 0)
            {
                throw new UnsafeTenantKeyConstraintActivationException(missingCount);
            }

            _finalConstraintsActive = true;
        }
    }

    public void InsertFromLegacyWriter(long id, string tenantId, string externalKey)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(tenantId);
        ArgumentException.ThrowIfNullOrWhiteSpace(externalKey);

        lock (_gate)
        {
            var tenantKey = _legacyWriterCompatibilityEnabled
                ? TenantScopedKey.Create(tenantId, externalKey)
                : null;

            if (_finalConstraintsActive && tenantKey is null)
            {
                throw new UnsafeTenantKeyConstraintActivationException(1);
            }

            AddRecordUnderLock(new TenantRecord(id, tenantId, externalKey, tenantKey));
        }
    }

    public TenantRecord GetRecord(long id)
    {
        lock (_gate)
        {
            return _records.TryGetValue(id, out var record)
                ? record
                : throw new KeyNotFoundException($"Record {id} does not exist.");
        }
    }

    private void AddExistingRecord(TenantRecord record)
    {
        ArgumentNullException.ThrowIfNull(record);

        lock (_gate)
        {
            AddRecordUnderLock(record);
        }
    }

    private void AddRecordUnderLock(TenantRecord record)
    {
        if (_records.ContainsKey(record.Id))
        {
            throw new InvalidOperationException($"Record {record.Id} already exists.");
        }

        if (record.TenantKey is not null && _tenantKeyOwners.ContainsKey(record.TenantKey))
        {
            throw new TenantKeyUniqueConstraintException(record.TenantKey);
        }

        _records.Add(record.Id, record);
        if (record.TenantKey is not null)
        {
            _tenantKeyOwners.Add(record.TenantKey, record.Id);
        }
    }
}
