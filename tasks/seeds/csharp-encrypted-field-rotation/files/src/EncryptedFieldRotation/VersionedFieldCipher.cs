using System.Security.Cryptography;
using System.Text;

namespace EncryptedFieldRotation;

public sealed class VersionedFieldCipher
{
    private const int NonceSize = 12;
    private const int TagSize = 16;
    private readonly KeyRing _keyRing;

    public VersionedFieldCipher(KeyRing keyRing)
    {
        _keyRing = keyRing ?? throw new ArgumentNullException(nameof(keyRing));
    }

    public string ActiveKeyVersion => _keyRing.ActiveVersion;

    public string Encrypt(string recordId, string plaintext, string keyVersion)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(recordId);
        ArgumentNullException.ThrowIfNull(plaintext);
        ArgumentException.ThrowIfNullOrWhiteSpace(keyVersion);

        byte[] plaintextBytes = Encoding.UTF8.GetBytes(plaintext);
        byte[] nonce = RandomNumberGenerator.GetBytes(NonceSize);
        byte[] ciphertext = new byte[plaintextBytes.Length];
        byte[] tag = new byte[TagSize];
        byte[] associatedData = Encoding.UTF8.GetBytes(recordId);

        try
        {
            using var aes = new AesGcm(_keyRing.Get(keyVersion), TagSize);
            aes.Encrypt(nonce, plaintextBytes, ciphertext, tag, associatedData);

            byte[] envelope = new byte[NonceSize + TagSize + ciphertext.Length];
            nonce.CopyTo(envelope, 0);
            tag.CopyTo(envelope, NonceSize);
            ciphertext.CopyTo(envelope, NonceSize + TagSize);
            return Convert.ToBase64String(envelope);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintextBytes);
        }
    }

    public string Decrypt(string recordId, string envelopeText, string keyVersion)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(recordId);
        ArgumentNullException.ThrowIfNull(envelopeText);
        ArgumentException.ThrowIfNullOrWhiteSpace(keyVersion);

        byte[] envelope;
        try
        {
            envelope = Convert.FromBase64String(envelopeText);
        }
        catch (FormatException exception)
        {
            throw new CryptographicException("The encrypted field envelope is malformed.", exception);
        }

        if (envelope.Length < NonceSize + TagSize)
        {
            throw new CryptographicException("The encrypted field envelope is malformed.");
        }

        ReadOnlySpan<byte> nonce = envelope.AsSpan(0, NonceSize);
        ReadOnlySpan<byte> tag = envelope.AsSpan(NonceSize, TagSize);
        ReadOnlySpan<byte> ciphertext = envelope.AsSpan(NonceSize + TagSize);
        byte[] plaintextBytes = new byte[ciphertext.Length];
        byte[] associatedData = Encoding.UTF8.GetBytes(recordId);

        try
        {
            using var aes = new AesGcm(_keyRing.Get(keyVersion), TagSize);
            aes.Decrypt(nonce, ciphertext, tag, plaintextBytes, associatedData);
            return Encoding.UTF8.GetString(plaintextBytes);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintextBytes);
            CryptographicOperations.ZeroMemory(envelope);
        }
    }
}
