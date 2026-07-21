using Microsoft.Extensions.Configuration;

namespace MoonRelay.Configuration;

public static class RelayOptionsResolver
{
    public static RelayOptionsResolution Resolve(
        ConfigurationLayer appSettings,
        ConfigurationLayer environment,
        ConfigurationLayer commandLine)
    {
        ArgumentNullException.ThrowIfNull(appSettings);
        ArgumentNullException.ThrowIfNull(environment);
        ArgumentNullException.ThrowIfNull(commandLine);

        EnsureSource(appSettings, ConfigurationSource.AppSettings);
        EnsureSource(environment, ConfigurationSource.Environment);
        EnsureSource(commandLine, ConfigurationSource.CommandLine);

        // Most-specific values are visited first so they cannot be displaced by
        // a provider intended only as a fallback.
        ConfigurationLayer[] layers = [commandLine, environment, appSettings];

        var options = new RelayOptions();
        foreach (var layer in layers)
        {
            IConfigurationRoot values = new ConfigurationBuilder()
                .AddInMemoryCollection(layer.Values)
                .Build();

            values.GetSection("Relay").Bind(options);
        }

        return new RelayOptionsResolution(options, Array.Empty<CompatibilityDiagnostic>());
    }

    private static void EnsureSource(ConfigurationLayer layer, ConfigurationSource expected)
    {
        ArgumentNullException.ThrowIfNull(layer.Values);
        if (layer.Source != expected)
        {
            throw new ArgumentException(
                $"Expected a {expected} layer but received {layer.Source}.",
                nameof(layer));
        }
    }
}
