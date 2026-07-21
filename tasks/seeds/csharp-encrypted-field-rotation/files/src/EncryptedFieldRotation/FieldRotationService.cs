using System.Security.Cryptography;

namespace EncryptedFieldRotation;

public sealed record RotationResult(
    int Examined,
    int Rotated,
    string? LastCheckpoint,
    string? AuthenticationFailureRecord,
    string? ConcurrencyConflictRecord);

public sealed class FieldRotationService
{
    private readonly ISecretStore _secrets;
    private readonly IRotationCheckpointStore _checkpoints;
    private readonly VersionedFieldCipher _cipher;
    private readonly IRotationLog _log;

    public FieldRotationService(
        ISecretStore secrets,
        IRotationCheckpointStore checkpoints,
        VersionedFieldCipher cipher,
        IRotationLog log)
    {
        _secrets = secrets ?? throw new ArgumentNullException(nameof(secrets));
        _checkpoints = checkpoints ?? throw new ArgumentNullException(nameof(checkpoints));
        _cipher = cipher ?? throw new ArgumentNullException(nameof(cipher));
        _log = log ?? throw new ArgumentNullException(nameof(log));
    }

    public RotationResult Rotate(int batchSize = 100)
    {
        if (batchSize <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(batchSize));
        }

        string? checkpoint = _checkpoints.Load();
        int examined = 0;
        int rotated = 0;

        while (true)
        {
            IReadOnlyList<PersistedSecret> batch = _secrets.ReadAfter(checkpoint, batchSize);
            if (batch.Count == 0)
            {
                return new RotationResult(examined, rotated, checkpoint, null, null);
            }

            foreach (PersistedSecret record in batch)
            {
                examined++;

                if (StringComparer.Ordinal.Equals(record.KeyVersion, _cipher.ActiveKeyVersion))
                {
                    _checkpoints.Save(record.Id);
                    checkpoint = record.Id;
                    continue;
                }

                string plaintext;
                try
                {
                    plaintext = _cipher.Decrypt(record.Id, record.Ciphertext, record.KeyVersion);
                }
                catch (CryptographicException)
                {
                    _log.AuthenticationFailed(record.Id, record.KeyVersion);
                    return new RotationResult(examined, rotated, checkpoint, record.Id, null);
                }

                string replacementCiphertext = _cipher.Encrypt(
                    record.Id,
                    plaintext,
                    _cipher.ActiveKeyVersion);

                // The ciphertext and its version are one logical value and must move together.
                bool replaced = _secrets.TryReplace(
                    record.Id,
                    record.Revision,
                    replacementCiphertext,
                    record.KeyVersion);

                if (!replaced)
                {
                    _log.ConcurrencyConflict(record.Id);
                    return new RotationResult(examined, rotated, checkpoint, null, record.Id);
                }

                _checkpoints.Save(record.Id);
                checkpoint = record.Id;
                rotated++;
                _log.Rotated(record.Id, record.KeyVersion, _cipher.ActiveKeyVersion);
            }
        }
    }
}
