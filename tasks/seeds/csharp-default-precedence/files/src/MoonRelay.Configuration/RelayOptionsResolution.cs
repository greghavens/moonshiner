namespace MoonRelay.Configuration;

public sealed record RelayOptionsResolution(
    RelayOptions Options,
    IReadOnlyList<CompatibilityDiagnostic> Diagnostics);
