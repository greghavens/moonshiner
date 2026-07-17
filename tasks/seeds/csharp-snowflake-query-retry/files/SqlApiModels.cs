namespace SnowflakeSql;

/// <summary>One SQL statement plus its execution context (SQL API v2 body).</summary>
public sealed record SqlStatement
{
    public required string Statement { get; init; }
    public int? Timeout { get; init; }
    public string? Database { get; init; }
    public string? Schema { get; init; }
    public string? Warehouse { get; init; }
    public string? Role { get; init; }
}

public sealed record ColumnInfo(string Name, string Type, bool Nullable);

/// <summary>A parsed 200 ResultSet. Row values stay string-encoded; SQL NULL is null.</summary>
public sealed record StatementResult(
    string StatementHandle,
    string Code,
    string SqlState,
    string Message,
    long NumRows,
    IReadOnlyList<ColumnInfo> Columns,
    IReadOnlyList<IReadOnlyList<string?>> Rows);

/// <summary>A parsed 202 QueryStatus: execution continues server-side.</summary>
public sealed record PendingStatement(
    string StatementHandle,
    string StatementStatusUrl,
    string Code,
    string Message);

/// <summary>
/// Transport-level outcome of one SQL API round trip. Terminal client errors
/// (400/401/403/422) throw <see cref="SnowflakeSqlException"/> instead; every
/// other status is reported here so a policy layer can decide what to do.
/// </summary>
public sealed record SqlApiOutcome
{
    public required int StatusCode { get; init; }
    public StatementResult? Result { get; init; }
    public PendingStatement? Pending { get; init; }
    public int? RetryAfterSeconds { get; init; }
}
