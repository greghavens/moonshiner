using System.Text.Json;

namespace MobileOfflineOutbox;

public sealed class JsonFileOutboxStore
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true
    };

    private readonly object _gate = new();
    private readonly string _path;
    private readonly Func<Guid> _newOperationId;
    private readonly List<OutboxEntry> _entries = new();
    private long _nextSequence;

    public JsonFileOutboxStore(
        string path,
        Func<Guid>? newOperationId = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);

        _path = Path.GetFullPath(path);
        _newOperationId = newOperationId ?? Guid.NewGuid;
        Load();
    }

    public OutboxEntry Enqueue(
        string documentId,
        int baseVersion,
        string body)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(documentId);
        ArgumentOutOfRangeException.ThrowIfNegative(baseVersion);
        ArgumentNullException.ThrowIfNull(body);

        lock (_gate)
        {
            Guid operationId = NextUniqueOperationId();
            var entry = new OutboxEntry(
                operationId,
                _nextSequence++,
                documentId,
                baseVersion,
                body,
                OutboxEntryState.Pending,
                Conflict: null);

            _entries.Add(entry);
            Persist();
            return entry;
        }
    }

    public IReadOnlyList<OutboxEntry> Snapshot()
    {
        lock (_gate)
        {
            return _entries.ToArray();
        }
    }

    public OutboxEntry ResolveConflict(
        Guid operationId,
        int newBaseVersion,
        string mergedBody)
    {
        ArgumentOutOfRangeException.ThrowIfNegative(newBaseVersion);
        ArgumentNullException.ThrowIfNull(mergedBody);

        lock (_gate)
        {
            int index = _entries.FindIndex(entry => entry.OperationId == operationId);
            if (index < 0 || _entries[index].State != OutboxEntryState.Conflict)
            {
                throw new InvalidOperationException(
                    $"Operation '{operationId}' is not a queued conflict.");
            }

            OutboxEntry resolved = _entries[index] with
            {
                OperationId = NextUniqueOperationId(),
                BaseVersion = newBaseVersion,
                Body = mergedBody,
                State = OutboxEntryState.Pending,
                Conflict = null
            };

            _entries[index] = resolved;
            Persist();
            return resolved;
        }
    }

    internal OutboxEntry? Head()
    {
        lock (_gate)
        {
            return _entries.Count == 0 ? null : _entries[0];
        }
    }

    internal void AcknowledgeHead(Guid operationId)
    {
        lock (_gate)
        {
            RequireHead(operationId);
            _entries.RemoveAt(0);
            Persist();
        }
    }

    internal void RecordHeadConflict(
        Guid operationId,
        int serverVersion,
        string serverBody)
    {
        lock (_gate)
        {
            RequireHead(operationId);
            _entries[0] = _entries[0] with
            {
                State = OutboxEntryState.Conflict,
                Conflict = new OutboxConflict(serverVersion, serverBody)
            };
            Persist();
        }
    }

    private void Load()
    {
        if (!File.Exists(_path))
        {
            return;
        }

        StoredOutbox? stored;
        try
        {
            byte[] json = File.ReadAllBytes(_path);
            stored = JsonSerializer.Deserialize<StoredOutbox>(json, JsonOptions);
        }
        catch (JsonException exception)
        {
            throw new InvalidDataException("The outbox file is invalid.", exception);
        }

        if (stored is null ||
            stored.NextSequence < 0 ||
            stored.Entries is null ||
            stored.Entries.Any(entry => entry is null) ||
            stored.Entries.Select(entry => entry.OperationId).Distinct().Count() != stored.Entries.Count ||
            stored.Entries.Select(entry => entry.Sequence).Distinct().Count() != stored.Entries.Count ||
            stored.Entries.Any(entry => entry.Sequence < 0 || entry.Sequence >= stored.NextSequence) ||
            !stored.Entries.SequenceEqual(stored.Entries.OrderBy(entry => entry.Sequence)) ||
            stored.Entries.Any(entry =>
                (entry.State == OutboxEntryState.Conflict) != (entry.Conflict is not null)))
        {
            throw new InvalidDataException("The outbox file is inconsistent.");
        }

        _nextSequence = stored.NextSequence;
        _entries.AddRange(stored.Entries);
    }

    private void Persist()
    {
        string? directory = Path.GetDirectoryName(_path);
        if (directory is not null)
        {
            Directory.CreateDirectory(directory);
        }

        byte[] json = JsonSerializer.SerializeToUtf8Bytes(
            new StoredOutbox(_nextSequence, _entries),
            JsonOptions);
        string temporaryPath = _path + ".new";

        using (var stream = new FileStream(
                   temporaryPath,
                   FileMode.Create,
                   FileAccess.Write,
                   FileShare.None,
                   bufferSize: 4096,
                   FileOptions.WriteThrough))
        {
            stream.Write(json);
            stream.Flush(flushToDisk: true);
        }

        File.Move(temporaryPath, _path, overwrite: true);
    }

    private Guid NextUniqueOperationId()
    {
        Guid operationId = _newOperationId();
        if (operationId == Guid.Empty ||
            _entries.Any(entry => entry.OperationId == operationId))
        {
            throw new InvalidOperationException(
                "The operation id source returned an empty or duplicate id.");
        }

        return operationId;
    }

    private void RequireHead(Guid operationId)
    {
        if (_entries.Count == 0 || _entries[0].OperationId != operationId)
        {
            throw new InvalidOperationException(
                "The durable outbox head changed during reconciliation.");
        }
    }

    private sealed record StoredOutbox(
        long NextSequence,
        List<OutboxEntry> Entries);
}
