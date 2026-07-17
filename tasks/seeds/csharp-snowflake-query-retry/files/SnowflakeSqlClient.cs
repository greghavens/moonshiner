using System.Text;
using System.Text.Json.Nodes;

namespace SnowflakeSql;

/// <summary>
/// Thin single-shot client for the Snowflake SQL API v2. One method call is
/// one HTTP round trip: no retries, no polling — that policy belongs to the
/// layer above. Terminal client errors (400/401/403/422) throw; everything
/// else is returned as a <see cref="SqlApiOutcome"/>.
/// </summary>
public sealed class SnowflakeSqlClient
{
    public const string StatementsPath = "/api/v2/statements";

    private readonly HttpClient _http;
    private readonly Uri _baseUrl;
    private readonly string _token;
    private readonly string _tokenType;
    private readonly string _userAgent;

    public SnowflakeSqlClient(HttpClient http, Uri baseUrl, string token, string tokenType, string userAgent)
    {
        _http = http;
        _baseUrl = baseUrl;
        _token = token;
        _tokenType = tokenType;
        _userAgent = userAgent;
    }

    public async Task<SqlApiOutcome> SubmitAsync(
        SqlStatement stmt, string requestId, bool retry, CancellationToken ct = default)
    {
        var query = "requestId=" + Uri.EscapeDataString(requestId);
        if (retry)
            query += "&retry=true";
        var uri = new Uri(_baseUrl, StatementsPath + "?" + query);

        var body = new JsonObject { ["statement"] = stmt.Statement };
        if (stmt.Timeout is int timeout)
            body["timeout"] = timeout;
        if (stmt.Database is not null)
            body["database"] = stmt.Database;
        if (stmt.Schema is not null)
            body["schema"] = stmt.Schema;
        if (stmt.Warehouse is not null)
            body["warehouse"] = stmt.Warehouse;
        if (stmt.Role is not null)
            body["role"] = stmt.Role;

        using var req = new HttpRequestMessage(HttpMethod.Post, uri)
        {
            Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json"),
        };
        return await SendAsync(req, ct).ConfigureAwait(false);
    }

    public async Task<SqlApiOutcome> CheckStatusAsync(string statementStatusUrl, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, new Uri(_baseUrl, statementStatusUrl));
        return await SendAsync(req, ct).ConfigureAwait(false);
    }

    private async Task<SqlApiOutcome> SendAsync(HttpRequestMessage req, CancellationToken ct)
    {
        req.Headers.TryAddWithoutValidation("Authorization", "Bearer " + _token);
        req.Headers.TryAddWithoutValidation("X-Snowflake-Authorization-Token-Type", _tokenType);
        req.Headers.TryAddWithoutValidation("Accept", "application/json");
        req.Headers.TryAddWithoutValidation("User-Agent", _userAgent);

        using var resp = await _http.SendAsync(req, ct).ConfigureAwait(false);
        var text = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
        var status = (int)resp.StatusCode;
        switch (status)
        {
            case 200:
                return new SqlApiOutcome { StatusCode = 200, Result = ParseResult(text) };
            case 202:
                return new SqlApiOutcome { StatusCode = 202, Pending = ParsePending(text) };
            case 400:
            case 401:
            case 403:
                throw new SnowflakeSqlException(status, ExtractMessage(text));
            case 422:
            {
                var node = JsonNode.Parse(text)?.AsObject()
                    ?? throw new SnowflakeSqlException(422, "unparseable QueryFailureStatus");
                throw new SnowflakeSqlException(
                    422,
                    (string?)node["message"] ?? "statement execution failed",
                    (string?)node["code"],
                    (string?)node["sqlState"],
                    (string?)node["statementHandle"]);
            }
            default:
            {
                int? retryAfter = null;
                if (resp.Headers.TryGetValues("Retry-After", out var values)
                    && int.TryParse(values.FirstOrDefault(), out var seconds))
                    retryAfter = seconds;
                return new SqlApiOutcome { StatusCode = status, RetryAfterSeconds = retryAfter };
            }
        }
    }

    private static StatementResult ParseResult(string text)
    {
        var node = JsonNode.Parse(text)!.AsObject();
        var meta = node["resultSetMetaData"]!.AsObject();
        var columns = new List<ColumnInfo>();
        foreach (var col in meta["rowType"]!.AsArray())
        {
            var c = col!.AsObject();
            columns.Add(new ColumnInfo(
                (string)c["name"]!, (string)c["type"]!, (bool)c["nullable"]!));
        }
        var rows = new List<IReadOnlyList<string?>>();
        foreach (var row in node["data"]!.AsArray())
        {
            var cells = new List<string?>();
            foreach (var cell in row!.AsArray())
                cells.Add(cell is null ? null : (string)cell!);
            rows.Add(cells);
        }
        return new StatementResult(
            (string)node["statementHandle"]!,
            (string)node["code"]!,
            (string?)node["sqlState"] ?? "",
            (string?)node["message"] ?? "",
            (long)meta["numRows"]!,
            columns,
            rows);
    }

    private static PendingStatement ParsePending(string text)
    {
        var node = JsonNode.Parse(text)!.AsObject();
        return new PendingStatement(
            (string)node["statementHandle"]!,
            (string)node["statementStatusUrl"]!,
            (string?)node["code"] ?? "",
            (string?)node["message"] ?? "");
    }

    private static string ExtractMessage(string text)
    {
        try
        {
            var node = JsonNode.Parse(text)?.AsObject();
            if (node?["message"] is JsonNode msg)
                return (string)msg!;
        }
        catch (System.Text.Json.JsonException)
        {
            // fall through to the generic message
        }
        return "no error message in response body";
    }
}
