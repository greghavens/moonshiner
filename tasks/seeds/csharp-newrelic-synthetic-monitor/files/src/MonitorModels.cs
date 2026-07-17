namespace Acme.NewRelic.Synthetics;

/// <summary>Desired state for one scripted API synthetic monitor.</summary>
public sealed record DesiredScriptApiMonitor(
    string Name,
    string Period,
    string Status,
    IReadOnlyList<string> PublicLocations,
    string? Script,
    double? ApdexTarget,
    IReadOnlyDictionary<string, IReadOnlyList<string>> Tags)
{
    /// <summary>When set, discovery is skipped and the monitor is addressed directly.</summary>
    public string? Guid { get; init; }
}

/// <summary>A monitor entity found through entity search.</summary>
public sealed record DiscoveredMonitor(
    string Guid,
    string Name,
    long AccountId,
    string MonitorType,
    IReadOnlyDictionary<string, IReadOnlyList<string>> Tags);

/// <summary>One per-field error returned by a synthetics mutation.</summary>
public sealed record MutationError(string Description, string Type);

/// <summary>What one reconcile pass did.</summary>
public sealed record ReconcileOutcome(
    string Action,
    string Guid,
    IReadOnlyList<string> TagKeysReplaced,
    IReadOnlyList<string> TagErrors);
