using System.Security.Cryptography;
using EncryptedFieldRotation;

var tests = new (string Name, Action Body)[]
{
    ("old and active key versions decrypt", OldAndActiveKeyVersionsDecrypt),
    ("rotation persists active metadata with ciphertext", RotationPersistsActiveMetadata),
    ("authentication failures stop without mutation or checkpoint", AuthenticationFailureIsSafe),
    ("rotation resumes after replacement beats checkpoint", RotationResumesAfterCheckpointFailure),
    ("successful rotation never logs plaintext", SuccessfulRotationDoesNotLogPlaintext)
};

int failed = 0;
foreach ((string name, Action body) in tests)
{
    try
    {
        body();
        Console.WriteLine($"PASS {name}");
    }
    catch (Exception exception)
    {
        failed++;
        Console.Error.WriteLine($"FAIL {name}: {exception.Message}");
    }
}

Console.WriteLine($"{tests.Length - failed}/{tests.Length} tests passed");
return failed == 0 ? 0 : 1;

static void OldAndActiveKeyVersionsDecrypt()
{
    VersionedFieldCipher cipher = CreateCipher();

    string oldEnvelope = cipher.Encrypt("row-a", "legacy-value", "v1");
    string activeEnvelope = cipher.Encrypt("row-b", "current-value", "v2");

    Check.Equal("legacy-value", cipher.Decrypt("row-a", oldEnvelope, "v1"));
    Check.Equal("current-value", cipher.Decrypt("row-b", activeEnvelope, "v2"));
    Check.Throws<CryptographicException>(() => cipher.Decrypt("row-a", Tamper(oldEnvelope), "v1"));
}

static void RotationPersistsActiveMetadata()
{
    VersionedFieldCipher cipher = CreateCipher();
    var store = new MemorySecretStore(
        new PersistedSecret("001", cipher.Encrypt("001", "alpha", "v1"), "v1", 4),
        new PersistedSecret("002", cipher.Encrypt("002", "beta", "v2"), "v2", 8));
    var checkpoints = new MemoryCheckpointStore();
    var log = new RecordingLog();

    RotationResult result = new FieldRotationService(store, checkpoints, cipher, log).Rotate(batchSize: 1);

    PersistedSecret first = store.Get("001");
    PersistedSecret second = store.Get("002");
    Check.Equal(2, result.Examined);
    Check.Equal(1, result.Rotated);
    Check.Equal("002", result.LastCheckpoint);
    Check.Equal("v2", first.KeyVersion);
    Check.Equal("alpha", cipher.Decrypt(first.Id, first.Ciphertext, first.KeyVersion));
    Check.Equal(5L, first.Revision);
    Check.Equal(8L, second.Revision);
}

static void AuthenticationFailureIsSafe()
{
    const string plaintext = "do-not-print-this-secret";
    VersionedFieldCipher cipher = CreateCipher();
    string damaged = Tamper(cipher.Encrypt("001", plaintext, "v1"));
    var original = new PersistedSecret("001", damaged, "v1", 3);
    var store = new MemorySecretStore(original);
    var checkpoints = new MemoryCheckpointStore();
    var log = new RecordingLog();

    RotationResult result = new FieldRotationService(store, checkpoints, cipher, log).Rotate();

    Check.Equal("001", result.AuthenticationFailureRecord);
    Check.Equal(0, result.Rotated);
    Check.Equal<string?>(null, checkpoints.Load());
    Check.Equal(original, store.Get("001"));
    Check.Contains("001", log.Text);
    Check.Contains("v1", log.Text);
    Check.DoesNotContain(plaintext, log.Text);
    Check.DoesNotContain(damaged, log.Text);
}

static void RotationResumesAfterCheckpointFailure()
{
    VersionedFieldCipher cipher = CreateCipher();
    var store = new MemorySecretStore(
        new PersistedSecret("001", cipher.Encrypt("001", "first secret", "v1"), "v1", 0),
        new PersistedSecret("002", cipher.Encrypt("002", "second secret", "v1"), "v1", 0));
    var checkpoints = new MemoryCheckpointStore(failOnceFor: "001");
    var log = new RecordingLog();
    var service = new FieldRotationService(store, checkpoints, cipher, log);

    Check.Throws<CheckpointUnavailableException>(() => service.Rotate(batchSize: 1));
    Check.Equal<string?>(null, checkpoints.Load());
    Check.Equal(1L, store.Get("001").Revision);

    RotationResult resumed = service.Rotate(batchSize: 1);

    Check.Equal("002", resumed.LastCheckpoint);
    Check.Equal("v2", store.Get("001").KeyVersion);
    Check.Equal("v2", store.Get("002").KeyVersion);
    Check.Equal("first secret", cipher.Decrypt("001", store.Get("001").Ciphertext, "v2"));
    Check.Equal("second secret", cipher.Decrypt("002", store.Get("002").Ciphertext, "v2"));
    Check.Equal(1L, store.Get("001").Revision);
    Check.Equal(1L, store.Get("002").Revision);
}

static void SuccessfulRotationDoesNotLogPlaintext()
{
    const string plaintext = "customer-access-token-4829";
    VersionedFieldCipher cipher = CreateCipher();
    var store = new MemorySecretStore(
        new PersistedSecret("customer-17", cipher.Encrypt("customer-17", plaintext, "v1"), "v1", 0));
    var log = new RecordingLog();

    new FieldRotationService(store, new MemoryCheckpointStore(), cipher, log).Rotate();

    Check.Contains("customer-17", log.Text);
    Check.Contains("v1", log.Text);
    Check.Contains("v2", log.Text);
    Check.DoesNotContain(plaintext, log.Text);
    Check.DoesNotContain(store.Get("customer-17").Ciphertext, log.Text);
}

static VersionedFieldCipher CreateCipher()
{
    var keys = new Dictionary<string, byte[]>(StringComparer.Ordinal)
    {
        ["v1"] = Convert.FromHexString("000102030405060708090A0B0C0D0E0F101112131415161718191A1B1C1D1E1F"),
        ["v2"] = Convert.FromHexString("F0E0D0C0B0A090807060504030201000FFEEDDCCBBAA99887766554433221100")
    };

    return new VersionedFieldCipher(new KeyRing("v2", keys));
}

static string Tamper(string envelopeText)
{
    byte[] bytes = Convert.FromBase64String(envelopeText);
    bytes[^1] ^= 0x40;
    return Convert.ToBase64String(bytes);
}

internal sealed class MemorySecretStore : ISecretStore
{
    private readonly SortedDictionary<string, PersistedSecret> _records;

    public MemorySecretStore(params PersistedSecret[] records)
    {
        _records = new SortedDictionary<string, PersistedSecret>(StringComparer.Ordinal);
        foreach (PersistedSecret record in records)
        {
            _records.Add(record.Id, record);
        }
    }

    public IReadOnlyList<PersistedSecret> ReadAfter(string? checkpoint, int batchSize) =>
        _records.Values
            .Where(record => checkpoint is null || StringComparer.Ordinal.Compare(record.Id, checkpoint) > 0)
            .Take(batchSize)
            .ToArray();

    public bool TryReplace(
        string id,
        long expectedRevision,
        string ciphertext,
        string keyVersion)
    {
        if (!_records.TryGetValue(id, out PersistedSecret? current) ||
            current.Revision != expectedRevision)
        {
            return false;
        }

        _records[id] = current with
        {
            Ciphertext = ciphertext,
            KeyVersion = keyVersion,
            Revision = current.Revision + 1
        };
        return true;
    }

    public PersistedSecret Get(string id) => _records[id];
}

internal sealed class MemoryCheckpointStore : IRotationCheckpointStore
{
    private readonly string? _failOnceFor;
    private bool _hasFailed;
    private string? _checkpoint;

    public MemoryCheckpointStore(string? failOnceFor = null)
    {
        _failOnceFor = failOnceFor;
    }

    public string? Load() => _checkpoint;

    public void Save(string recordId)
    {
        if (!_hasFailed && StringComparer.Ordinal.Equals(recordId, _failOnceFor))
        {
            _hasFailed = true;
            throw new CheckpointUnavailableException();
        }

        _checkpoint = recordId;
    }
}

internal sealed class CheckpointUnavailableException : Exception
{
}

internal sealed class RecordingLog : IRotationLog
{
    private readonly List<string> _entries = new();

    public string Text => string.Join("\n", _entries);

    public void Rotated(string recordId, string fromKeyVersion, string toKeyVersion) =>
        _entries.Add($"rotated id={recordId} from={fromKeyVersion} to={toKeyVersion}");

    public void AuthenticationFailed(string recordId, string keyVersion) =>
        _entries.Add($"authentication-failed id={recordId} version={keyVersion}");

    public void ConcurrencyConflict(string recordId) =>
        _entries.Add($"concurrency-conflict id={recordId}");
}

internal static class Check
{
    public static void Equal<T>(T expected, T actual)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new InvalidOperationException($"Expected <{expected}> but found <{actual}>.");
        }
    }

    public static void Contains(string expected, string actual)
    {
        if (!actual.Contains(expected, StringComparison.Ordinal))
        {
            throw new InvalidOperationException($"Expected text to contain <{expected}>.");
        }
    }

    public static void DoesNotContain(string unexpected, string actual)
    {
        if (actual.Contains(unexpected, StringComparison.Ordinal))
        {
            throw new InvalidOperationException("Text contained a forbidden value.");
        }
    }

    public static void Throws<TException>(Action action)
        where TException : Exception
    {
        try
        {
            action();
        }
        catch (TException)
        {
            return;
        }

        throw new InvalidOperationException($"Expected {typeof(TException).Name}.");
    }
}
