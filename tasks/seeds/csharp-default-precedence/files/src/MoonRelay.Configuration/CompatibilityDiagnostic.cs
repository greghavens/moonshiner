namespace MoonRelay.Configuration;

public sealed record CompatibilityDiagnostic(
    string Code,
    string OptionPath,
    ConfigurationSource SelectedSource,
    ConfigurationSource ShadowedSource,
    string Message);
