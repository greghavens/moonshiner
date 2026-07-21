namespace MoonRelay.Configuration;

public sealed record ConfigurationLayer(
    ConfigurationSource Source,
    IReadOnlyDictionary<string, string?> Values);
