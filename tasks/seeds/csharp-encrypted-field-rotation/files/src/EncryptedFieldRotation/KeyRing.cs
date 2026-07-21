using System.Security.Cryptography;

namespace EncryptedFieldRotation;

public sealed class KeyRing
{
    private readonly IReadOnlyDictionary<string, byte[]> _keys;

    public KeyRing(string activeVersion, IReadOnlyDictionary<string, byte[]> keys)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(activeVersion);
        ArgumentNullException.ThrowIfNull(keys);

        _keys = keys.ToDictionary(
            pair => pair.Key,
            pair => pair.Value.ToArray(),
            StringComparer.Ordinal);

        if (!_keys.ContainsKey(activeVersion))
        {
            throw new ArgumentException("The active key version is not present.", nameof(activeVersion));
        }

        foreach ((string version, byte[] key) in _keys)
        {
            if (string.IsNullOrWhiteSpace(version))
            {
                throw new ArgumentException("Key versions cannot be blank.", nameof(keys));
            }

            if (key.Length is not (16 or 24 or 32))
            {
                throw new ArgumentException("AES keys must be 128, 192, or 256 bits.", nameof(keys));
            }
        }

        ActiveVersion = activeVersion;
    }

    public string ActiveVersion { get; }

    public byte[] Get(string version)
    {
        if (!_keys.TryGetValue(version, out byte[]? key) || key is null)
        {
            throw new CryptographicException($"No key is available for version '{version}'.");
        }

        return key;
    }
}
