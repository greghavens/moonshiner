namespace SnowflakeSql;

/// <summary>
/// Terminal SQL API failure. For 422 responses the Snowflake error identity
/// (code, sqlState, statementHandle) is preserved from the QueryFailureStatus.
/// The bearer token must never appear in <see cref="Exception.Message"/>.
/// </summary>
public sealed class SnowflakeSqlException : Exception
{
    public int StatusCode { get; }
    public string? Code { get; }
    public string? SqlState { get; }
    public string? StatementHandle { get; }

    public SnowflakeSqlException(
        int statusCode,
        string message,
        string? code = null,
        string? sqlState = null,
        string? statementHandle = null)
        : base(code is null
            ? $"SQL API request failed with HTTP {statusCode}: {message}"
            : $"SQL API statement failed (code {code}, sqlState {sqlState}): {message}")
    {
        StatusCode = statusCode;
        Code = code;
        SqlState = sqlState;
        StatementHandle = statementHandle;
    }
}
