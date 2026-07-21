using CentralLock.Core;

#if WINDOWS
using Moon.Windows.CredentialStore;
#endif

namespace CentralLock.Desktop;

public static class DesktopIdentity
{
    public static string Describe(string name)
    {
        var normalized = JsonFormatter.NormalizeName(name);
#if WINDOWS
        return $"{normalized}:{CredentialScope.Current}";
#else
        return $"{normalized}:Portable";
#endif
    }
}
