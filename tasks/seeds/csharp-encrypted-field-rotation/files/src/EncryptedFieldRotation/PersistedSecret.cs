namespace EncryptedFieldRotation;

public sealed record PersistedSecret(
    string Id,
    string Ciphertext,
    string KeyVersion,
    long Revision);

public interface ISecretStore
{
    IReadOnlyList<PersistedSecret> ReadAfter(string? checkpoint, int batchSize);

    bool TryReplace(
        string id,
        long expectedRevision,
        string ciphertext,
        string keyVersion);
}

public interface IRotationCheckpointStore
{
    string? Load();

    void Save(string recordId);
}

public interface IRotationLog
{
    void Rotated(string recordId, string fromKeyVersion, string toKeyVersion);

    void AuthenticationFailed(string recordId, string keyVersion);

    void ConcurrencyConflict(string recordId);
}
