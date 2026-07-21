using System.Security.Cryptography;

namespace EncryptedFieldRotation;

public interface INonceSource
{
    void Fill(Span<byte> destination);
}

public sealed class CryptographicNonceSource : INonceSource
{
    public void Fill(Span<byte> destination) => RandomNumberGenerator.Fill(destination);
}
