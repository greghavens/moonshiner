namespace Foundry.Telemetry;

/// <summary>
/// One structured log record. Fields is a flat bag of custom values
/// (scalars only) that downstream sinks may index.
/// </summary>
public sealed record LogEvent(
    DateTimeOffset Timestamp,
    string Level,
    string Message,
    IReadOnlyDictionary<string, object?>? Fields = null);
