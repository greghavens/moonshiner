namespace MoonRelay.Configuration;

// Values are ordered from the least specific explicit provider to the most
// specific. Property initializers are fallbacks, not an explicit provider.
public enum ConfigurationSource
{
    AppSettings = 1,
    Environment = 2,
    CommandLine = 3
}
