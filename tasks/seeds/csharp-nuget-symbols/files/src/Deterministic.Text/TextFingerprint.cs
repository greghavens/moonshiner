using System.Text;

namespace Moonshiner.Deterministic.Text;

/// <summary>Creates a stable, non-cryptographic fingerprint for text.</summary>
public static class TextFingerprint
{
    /// <summary>Returns a lowercase FNV-1a fingerprint of the UTF-8 input.</summary>
    public static string Compute(string value)
    {
        ArgumentNullException.ThrowIfNull(value);

        const ulong offsetBasis = 14695981039346656037UL;
        const ulong prime = 1099511628211UL;
        var hash = offsetBasis;

        foreach (var octet in Encoding.UTF8.GetBytes(value))
        {
            hash ^= octet;
            hash *= prime;
        }

        return hash.ToString("x16");
    }
}
