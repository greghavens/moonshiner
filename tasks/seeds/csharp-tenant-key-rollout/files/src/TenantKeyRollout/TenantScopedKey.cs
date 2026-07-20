namespace TenantKeyRollout;

public static class TenantScopedKey
{
    public static string Create(string tenantId, string externalKey)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(tenantId);
        ArgumentException.ThrowIfNullOrWhiteSpace(externalKey);

        // Length-prefixing avoids ambiguous pairs such as ("ab", "c") and ("a", "bc").
        return $"{tenantId.Length}:{tenantId}{externalKey}";
    }
}
