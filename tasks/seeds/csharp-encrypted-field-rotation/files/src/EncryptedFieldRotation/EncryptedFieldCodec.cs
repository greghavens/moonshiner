using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;

namespace EncryptedFieldRotation;

public sealed class EncryptedFieldCodec
{
    private const int NonceSize = 12;
    private const int TagSize = 16;

    private readonly EncryptionKeyRing _keyRing;
    private readonly INonceSource _nonceSource;

    public EncryptedFieldCodec(EncryptionKeyRing keyRing, INonceSource nonceSource)
    {
        _keyRing = keyRing;
        _nonceSource = nonceSource;
    }

    public EncryptedValue Encrypt(string plaintext, int? keyVersion = null)
    {
        ArgumentNullException.ThrowIfNull(plaintext);

        var version = keyVersion ?? _keyRing.CurrentVersion;
        var key = _keyRing.GetKey(version);
        var nonce = new byte[NonceSize];
        var plaintextBytes = Encoding.UTF8.GetBytes(plaintext);
        var ciphertext = new byte[plaintextBytes.Length];
        var tag = new byte[TagSize];

        _nonceSource.Fill(nonce);

        try
        {
            using var aes = new AesGcm(key, TagSize);
            aes.Encrypt(nonce, plaintextBytes, ciphertext, tag, AssociatedData(version));
            return new EncryptedValue(version, nonce, ciphertext, tag);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintextBytes);
        }
    }

    public string Decrypt(EncryptedValue encrypted)
    {
        ArgumentNullException.ThrowIfNull(encrypted);

        var key = _keyRing.GetKey(encrypted.KeyVersion);
        var plaintextBytes = new byte[encrypted.Ciphertext.Length];

        try
        {
            using var aes = new AesGcm(key, TagSize);
            aes.Decrypt(
                encrypted.Nonce,
                encrypted.Ciphertext,
                encrypted.AuthenticationTag,
                plaintextBytes,
                AssociatedData(encrypted.KeyVersion));
            return Encoding.UTF8.GetString(plaintextBytes);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintextBytes);
        }
    }

    private static byte[] AssociatedData(int version)
    {
        var associatedData = new byte[sizeof(int)];
        BinaryPrimitives.WriteInt32BigEndian(associatedData, version);
        return associatedData;
    }
}
