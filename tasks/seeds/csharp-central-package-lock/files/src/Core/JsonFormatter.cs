using Moon.Security.Json;

namespace CentralLock.Core;

public static class JsonFormatter
{
    public static string NormalizeName(string value) => JsonIdentity.Normalize(value);
}
