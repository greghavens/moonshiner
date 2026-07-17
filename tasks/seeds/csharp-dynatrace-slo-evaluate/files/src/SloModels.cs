namespace Acme.Dynatrace.Slo;

/// <summary>
/// One SLO criteria window: a relative timeframe plus the target and
/// optional warning thresholds (percentages).
/// </summary>
public sealed record SloCriteria(
    string TimeframeFrom,
    string? TimeframeTo,
    double Target,
    double? Warning);

/// <summary>
/// The desired shape of a service-level objective. The SLI is a custom
/// DQL indicator (latest SLOs express the SLI as a single DQL query).
/// </summary>
public sealed record SloDefinition(
    string Name,
    string? Description,
    string CustomSliIndicator,
    IReadOnlyList<SloCriteria> Criteria,
    IReadOnlyList<string> Tags,
    string? ExternalId);

/// <summary>A stored SLO as the API reports it back.</summary>
public sealed record SloRecord(string Id, string Name, string Version);

public enum SloStatus
{
    Success,
    Warning,
    Failure,
    Pending,
}

/// <summary>One normalized evaluation result row.</summary>
public sealed record EvaluationEntry(
    string Criteria,
    SloStatus Status,
    double? Value,
    double? ErrorBudget,
    string? Message);

/// <summary>A finished evaluation, normalized for reporting.</summary>
public sealed record EvaluationReport(
    string SloId,
    IReadOnlyList<EvaluationEntry> Entries,
    SloStatus Overall);
