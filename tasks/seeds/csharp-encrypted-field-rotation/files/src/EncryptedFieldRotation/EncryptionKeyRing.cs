namespace EncryptedFieldRotation;

public sealed class EncryptionKeyRing
{
    private readonly IReadOnlyDictionary<int, byte[]> _keys;

    public EncryptionKeyRing(int currentVersion, IReadOnlyDictionary<int, byte[]> keys)
    {
        if (!keys.ContainsKey(currentVersion))
        {
            throw new ArgumentException("The current key version must be present.", nameof(keys));
        }

        foreach (var (version, key) in keys)
        {
            if (version <= 0)
            {
                throw new ArgumentException("Key versions must be positive.", nameof(keys));
            }

            if (key.Length is not (16 or 24 or 32))
            {
                throw new ArgumentException("AES keys must contain 16, 24, or 32 bytes.", nameof(keys));
            }
        }

        CurrentVersion = currentVersion;
        _keys = keys.ToDictionary(pair => pair.Key, pair => pair.Value.ToArray());
    }

    public int CurrentVersion { get; }

    public byte[] GetKey(int version)
    {
        if (!_keys.TryGetValue(version, out var key))
        {
            throw new KeyNotFoundException($"Encryption key version {version} is unavailable.");
        }

        return key;
    }
}
