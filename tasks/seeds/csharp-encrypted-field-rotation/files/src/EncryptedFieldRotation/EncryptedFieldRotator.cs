namespace EncryptedFieldRotation;

public sealed record RotationReport(int Rotated, int Skipped, IReadOnlyList<Guid> FailedRecordIds);

public sealed class EncryptedFieldRotator
{
    private readonly ICustomerRepository _repository;
    private readonly EncryptedFieldCodec _codec;
    private readonly EncryptionKeyRing _keyRing;
    private readonly IRotationLogger _logger;

    public EncryptedFieldRotator(
        ICustomerRepository repository,
        EncryptedFieldCodec codec,
        EncryptionKeyRing keyRing,
        IRotationLogger logger)
    {
        _repository = repository;
        _codec = codec;
        _keyRing = keyRing;
        _logger = logger;
    }

    public RotationReport RotateBatch()
    {
        var rotated = 0;
        var skipped = 0;
        var failures = new List<Guid>();

        foreach (var record in _repository.ReadBatch())
        {
            if (record.Secret.KeyVersion == _keyRing.CurrentVersion)
            {
                skipped++;
                continue;
            }

            var plaintext = _codec.Decrypt(record.Secret);
            var replacement = _codec.Encrypt(plaintext);

            if (_repository.TryReplaceSecret(record.Id, record.Secret, replacement))
            {
                rotated++;
                _logger.Information(
                    "Rotated encrypted field for record {0} from key version {1} to {2}.",
                    record.Id,
                    record.Secret.KeyVersion,
                    replacement.KeyVersion);
            }
            else
            {
                failures.Add(record.Id);
                _logger.Warning(
                    "Encrypted field for record {0} changed concurrently; rotation was not applied.",
                    record.Id);
            }
        }

        return new RotationReport(rotated, skipped, failures);
    }
}
