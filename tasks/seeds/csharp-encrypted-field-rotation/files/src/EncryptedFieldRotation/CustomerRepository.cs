namespace EncryptedFieldRotation;

public sealed record CustomerRecord(Guid Id, EncryptedValue Secret);

public interface ICustomerRepository
{
    IReadOnlyList<CustomerRecord> ReadBatch();

    bool TryReplaceSecret(Guid id, EncryptedValue expected, EncryptedValue replacement);
}
