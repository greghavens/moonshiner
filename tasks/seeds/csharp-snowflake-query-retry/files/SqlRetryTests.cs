// Acceptance tests for the Snowflake SQL API transport.
//
// Runs a loopback fake SQL API v2 endpoint implementing the subset pinned in
// docs/contract.json. No vendor network, no real credentials, no real delays:
// the executor under test must route every wait through the injected delay
// function. Protected — do not modify.

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using SnowflakeSql;

namespace SnowflakeSqlTests;

public sealed class FakeSqlApi : IDisposable
{
    public const string Token = "dummy-oauth-91be44c2d0"; // dummy; must never leak
    public const string TokenType = "OAUTH";
    public const string UserAgent = "sfingest/1.0";
    public const string BasePath = "/api/v2/statements";
    public const string Handle = "01b70000-0000-4000-8000-00000000f00d";

    public sealed record Recorded(
        string Method, string Path, Dictionary<string, string> Query,
        Dictionary<string, string> Headers, string Body);

    // Kind: result | status202 | exec500 | 429 | 503 | 400 | 422 | 401
    public sealed record SubmitSpec(string Kind, int RetryAfter = 0);

    // Kind: 202 | result | 422
    public sealed record PollSpec(string Kind);

    public List<Recorded> Requests { get; } = new();
    public Queue<SubmitSpec> SubmitPlan { get; } = new();
    public Queue<PollSpec> PollPlan { get; } = new();
    public Dictionary<string, int> Executions { get; } = new();
    public string BaseUrl { get; }

    private readonly HttpListener _listener;
    private readonly Task _loop;

    public FakeSqlApi()
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        int port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        BaseUrl = $"http://127.0.0.1:{port}";
        _listener = new HttpListener();
        _listener.Prefixes.Add(BaseUrl + "/");
        _listener.Start();
        _loop = Task.Run(LoopAsync);
    }

    public static string ResultBody(string handle) => $$"""
        {
          "code": "090001",
          "sqlState": "00000",
          "message": "Statement executed successfully.",
          "statementHandle": "{{handle}}",
          "createdOn": 1752724800000,
          "statementStatusUrl": "{{BasePath}}/{{handle}}",
          "resultSetMetaData": {
            "numRows": 2,
            "format": "jsonv2",
            "rowType": [
              {"name": "ORDER_ID", "type": "FIXED", "length": 0, "precision": 38, "scale": 0, "nullable": false},
              {"name": "REGION", "type": "TEXT", "length": 16777216, "precision": 0, "scale": 0, "nullable": true},
              {"name": "TOTAL", "type": "FIXED", "length": 0, "precision": 12, "scale": 2, "nullable": false}
            ],
            "partitionInfo": [{"rowCount": 2, "uncompressedSize": 96}]
          },
          "data": [["7001", "emea", "249.90"], ["7002", null, "18.00"]]
        }
        """;

    public static string StatusBody(string handle) => $$"""
        {
          "code": "333334",
          "message": "Asynchronous execution in progress. Use provided query id to perform query monitoring and management.",
          "statementHandle": "{{handle}}",
          "statementStatusUrl": "{{BasePath}}/{{handle}}"
        }
        """;

    public static string FailureBody(string handle) => $$"""
        {
          "code": "001003",
          "sqlState": "42000",
          "message": "SQL compilation error:\nsyntax error line 1 at position 7 unexpected 'FORM'.",
          "statementHandle": "{{handle}}",
          "statementStatusUrl": "{{BasePath}}/{{handle}}"
        }
        """;

    private async Task LoopAsync()
    {
        while (_listener.IsListening)
        {
            HttpListenerContext ctx;
            try { ctx = await _listener.GetContextAsync(); }
            catch (Exception) { return; }
            try { Handle_(ctx); }
            catch (Exception) { TryRespond(ctx, 500, "{\"message\": \"fake broke\"}"); }
        }
    }

    private void Handle_(HttpListenerContext ctx)
    {
        var req = ctx.Request;
        string body;
        using (var reader = new StreamReader(req.InputStream, Encoding.UTF8))
            body = reader.ReadToEnd();
        var query = new Dictionary<string, string>();
        foreach (string? key in req.QueryString.AllKeys)
            if (key is not null)
                query[key] = req.QueryString[key]!;
        var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (string? key in req.Headers.AllKeys)
            if (key is not null)
                headers[key] = req.Headers[key]!;
        lock (Requests)
            Requests.Add(new Recorded(req.HttpMethod, req.Url!.AbsolutePath, query, headers, body));

        if (req.HttpMethod == "POST" && req.Url!.AbsolutePath == BasePath)
        {
            string rid = query.GetValueOrDefault("requestId", "");
            lock (Requests)
            {
                if (Executions.ContainsKey(rid) && query.GetValueOrDefault("retry") == "true")
                {
                    // documented resubmission semantics: same requestId + retry=true
                    // is answered from the original execution, never re-executed
                    TryRespond(ctx, 200, ResultBody(Handle));
                    return;
                }
                var spec = SubmitPlan.Count > 0 ? SubmitPlan.Dequeue() : new SubmitSpec("400");
                switch (spec.Kind)
                {
                    case "result":
                        Executions[rid] = Executions.GetValueOrDefault(rid) + 1;
                        TryRespond(ctx, 200, ResultBody(Handle));
                        break;
                    case "status202":
                        Executions[rid] = Executions.GetValueOrDefault(rid) + 1;
                        TryRespond(ctx, 202, StatusBody(Handle));
                        break;
                    case "exec500":
                        // the statement ran, then the response was lost server-side
                        Executions[rid] = Executions.GetValueOrDefault(rid) + 1;
                        TryRespond(ctx, 500, "{\"message\": \"Internal server error.\"}");
                        break;
                    case "429":
                        TryRespond(ctx, 429, "{\"message\": \"Number of requests exceeded the limit.\"}",
                            ("Retry-After", spec.RetryAfter.ToString()));
                        break;
                    case "503":
                        TryRespond(ctx, 503, "{\"message\": \"Service temporarily unavailable.\"}");
                        break;
                    case "400":
                        TryRespond(ctx, 400, "{\"message\": \"Unable to parse the request body.\"}");
                        break;
                    case "422":
                        TryRespond(ctx, 422, FailureBody(Handle));
                        break;
                    case "401":
                        TryRespond(ctx, 401, "{\"message\": \"Authorization token has expired.\"}");
                        break;
                    default:
                        TryRespond(ctx, 500, "{\"message\": \"bad spec\"}");
                        break;
                }
            }
            return;
        }

        if (req.HttpMethod == "GET" && req.Url!.AbsolutePath.StartsWith(BasePath + "/"))
        {
            lock (Requests)
            {
                var spec = PollPlan.Count > 0 ? PollPlan.Dequeue() : new PollSpec("202");
                switch (spec.Kind)
                {
                    case "202": TryRespond(ctx, 202, StatusBody(Handle)); break;
                    case "result": TryRespond(ctx, 200, ResultBody(Handle)); break;
                    case "422": TryRespond(ctx, 422, FailureBody(Handle)); break;
                    default: TryRespond(ctx, 500, "{\"message\": \"bad spec\"}"); break;
                }
            }
            return;
        }

        TryRespond(ctx, 404, "{\"message\": \"unknown endpoint\"}");
    }

    private static void TryRespond(HttpListenerContext ctx, int status, string body,
        params (string Name, string Value)[] extraHeaders)
    {
        try
        {
            ctx.Response.StatusCode = status;
            ctx.Response.ContentType = "application/json";
            foreach (var (name, value) in extraHeaders)
                ctx.Response.AddHeader(name, value);
            var bytes = Encoding.UTF8.GetBytes(body);
            ctx.Response.ContentLength64 = bytes.Length;
            ctx.Response.OutputStream.Write(bytes);
            ctx.Response.Close();
        }
        catch (Exception)
        {
            // client went away; nothing to do
        }
    }

    public List<Recorded> Posts()
    {
        lock (Requests) return Requests.Where(r => r.Method == "POST").ToList();
    }

    public List<Recorded> Gets()
    {
        lock (Requests) return Requests.Where(r => r.Method == "GET").ToList();
    }

    public void Dispose()
    {
        try { _listener.Stop(); _listener.Close(); } catch (Exception) { }
    }
}

public class SqlRetryTests
{
    private static readonly JsonDocument Contract = JsonDocument.Parse(
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "docs", "contract.json")));
    private static readonly JsonDocument Sources = JsonDocument.Parse(
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "docs", "official_sources.json")));

    private static Func<string> IdSeq()
    {
        int n = 0;
        return () => $"00000000-0000-4000-8000-{Interlocked.Increment(ref n):d12}";
    }

    private static SnowflakeSqlClient NewClient(FakeSqlApi fake) => new(
        new HttpClient(), new Uri(fake.BaseUrl), FakeSqlApi.Token,
        FakeSqlApi.TokenType, FakeSqlApi.UserAgent);

    private static (SnowflakeStatementExecutor Executor, List<TimeSpan> Delays) NewExecutor(FakeSqlApi fake)
    {
        var delays = new List<TimeSpan>();
        var executor = new SnowflakeStatementExecutor(
            NewClient(fake),
            new RetryOptions(),
            IdSeq(),
            (delay, ct) => { delays.Add(delay); return Task.CompletedTask; });
        return (executor, delays);
    }

    private static readonly SqlStatement FullStatement = new()
    {
        Statement = "insert into orders_staging select * from ext_orders where batch_id = 812",
        Timeout = 60,
        Database = "INGEST",
        Schema = "STAGING",
        Warehouse = "WH_LOAD",
        Role = "LOADER",
    };

    private static void CheckCommonHeaders(FakeSqlApi.Recorded r, bool post)
    {
        Assert.Equal("Bearer " + FakeSqlApi.Token, r.Headers.GetValueOrDefault("Authorization"));
        Assert.Equal(FakeSqlApi.TokenType, r.Headers.GetValueOrDefault("X-Snowflake-Authorization-Token-Type"));
        Assert.Equal("application/json", r.Headers.GetValueOrDefault("Accept"));
        Assert.Equal(FakeSqlApi.UserAgent, r.Headers.GetValueOrDefault("User-Agent"));
        if (post)
            Assert.StartsWith("application/json", r.Headers.GetValueOrDefault("Content-Type") ?? "");
    }

    [Fact]
    public void ProtectedFixturesIntact()
    {
        var research = Sources.RootElement.GetProperty("research");
        Assert.True(research.GetProperty("required").GetBoolean());
        var sources = research.GetProperty("official_sources").EnumerateArray().ToList();
        Assert.True(sources.Count >= 2, "at least two official sources required");
        foreach (var src in sources)
        {
            Assert.StartsWith("https://docs.snowflake.com/", src.GetProperty("url").GetString());
            Assert.False(string.IsNullOrEmpty(src.GetProperty("used_for").GetString()));
        }
        Assert.True(Sources.RootElement.GetProperty("verified_facts").GetArrayLength() >= 4);

        var root = Contract.RootElement;
        Assert.Equal(FakeSqlApi.BasePath, root.GetProperty("base_path").GetString());
        var policy = root.GetProperty("retry_policy");
        Assert.Equal(4, policy.GetProperty("max_attempts_total").GetInt32());
        Assert.Equal(new[] { 1, 2, 4 },
            policy.GetProperty("fallback_backoff_seconds").EnumerateArray().Select(e => e.GetInt32()).ToArray());
        var polling = root.GetProperty("polling");
        Assert.Equal(1, polling.GetProperty("interval_seconds").GetInt32());
        Assert.Equal(10, polling.GetProperty("max_polls").GetInt32());
    }

    [Fact]
    public async Task ClientSubmitSendsDocumentedShape()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("result"));
        var outcome = await NewClient(fake).SubmitAsync(FullStatement, "11111111-2222-4333-8444-555555555555", retry: false);
        Assert.Equal(200, outcome.StatusCode);

        var post = Assert.Single(fake.Posts());
        Assert.Equal(FakeSqlApi.BasePath, post.Path);
        CheckCommonHeaders(post, post: true);
        Assert.Equal("11111111-2222-4333-8444-555555555555", post.Query.GetValueOrDefault("requestId"));
        Assert.False(post.Query.ContainsKey("retry"), "a first attempt must not claim retry=true");

        using var body = JsonDocument.Parse(post.Body);
        var root = body.RootElement;
        Assert.Equal(6, root.EnumerateObject().Count());
        Assert.Equal(FullStatement.Statement, root.GetProperty("statement").GetString());
        Assert.Equal(60, root.GetProperty("timeout").GetInt32());
        Assert.Equal("INGEST", root.GetProperty("database").GetString());
        Assert.Equal("STAGING", root.GetProperty("schema").GetString());
        Assert.Equal("WH_LOAD", root.GetProperty("warehouse").GetString());
        Assert.Equal("LOADER", root.GetProperty("role").GetString());
    }

    [Fact]
    public async Task ClientOmitsUnsetFields()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("result"));
        await NewClient(fake).SubmitAsync(new SqlStatement { Statement = "select 1" },
            "11111111-2222-4333-8444-555555555555", retry: false);

        using var body = JsonDocument.Parse(Assert.Single(fake.Posts()).Body);
        Assert.Equal(1, body.RootElement.EnumerateObject().Count());
        Assert.Equal("select 1", body.RootElement.GetProperty("statement").GetString());
    }

    [Fact]
    public async Task ClientParsesResultSet()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("result"));
        var outcome = await NewClient(fake).SubmitAsync(new SqlStatement { Statement = "select 1" },
            "11111111-2222-4333-8444-555555555555", retry: false);

        var result = outcome.Result!;
        Assert.Equal(FakeSqlApi.Handle, result.StatementHandle);
        Assert.Equal("090001", result.Code);
        Assert.Equal(2, result.NumRows);
        Assert.Equal(new[] { "ORDER_ID", "REGION", "TOTAL" }, result.Columns.Select(c => c.Name).ToArray());
        Assert.Equal(new[] { false, true, false }, result.Columns.Select(c => c.Nullable).ToArray());
        Assert.Equal(new string?[] { "7001", "emea", "249.90" }, result.Rows[0].ToArray());
        Assert.Equal(new string?[] { "7002", null, "18.00" }, result.Rows[1].ToArray());
    }

    [Fact]
    public async Task ClientSurfaces422WithSnowflakeIdentity()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("422"));
        var ex = await Assert.ThrowsAsync<SnowflakeSqlException>(() =>
            NewClient(fake).SubmitAsync(new SqlStatement { Statement = "select * form t" },
                "11111111-2222-4333-8444-555555555555", retry: false));
        Assert.Equal(422, ex.StatusCode);
        Assert.Equal("001003", ex.Code);
        Assert.Equal("42000", ex.SqlState);
        Assert.Equal(FakeSqlApi.Handle, ex.StatementHandle);
        Assert.Contains("001003", ex.Message);
        Assert.Contains("42000", ex.Message);
        Assert.DoesNotContain(FakeSqlApi.Token, ex.Message);
    }

    [Fact]
    public async Task ExecutorRetries429HonoringRetryAfter()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("429", RetryAfter: 3));
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("result"));
        var (executor, delays) = NewExecutor(fake);

        var result = await executor.ExecuteAsync(FullStatement);
        Assert.Equal(FakeSqlApi.Handle, result.StatementHandle);

        Assert.Equal(new[] { TimeSpan.FromSeconds(3) }, delays.ToArray());
        var posts = fake.Posts();
        Assert.Equal(2, posts.Count);
        Assert.Equal(posts[0].Query["requestId"], posts[1].Query["requestId"]);
        Assert.False(posts[0].Query.ContainsKey("retry"));
        Assert.Equal("true", posts[1].Query.GetValueOrDefault("retry"));
        Assert.Equal(posts[0].Body, posts[1].Body);
        Assert.Equal(1, fake.Executions.GetValueOrDefault(posts[0].Query["requestId"]));
    }

    [Fact]
    public async Task ExecutorBoundedBackoffForTransient5xx()
    {
        using var fake = new FakeSqlApi();
        for (int i = 0; i < 6; i++)
            fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("503"));
        var (executor, delays) = NewExecutor(fake);

        var ex = await Assert.ThrowsAsync<SnowflakeTransportException>(() =>
            executor.ExecuteAsync(FullStatement));
        Assert.Equal(4, fake.Posts().Count);
        Assert.Equal(4, ex.Attempts);
        Assert.Equal(503, ex.LastStatusCode);
        Assert.Equal(new[] { TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(4) },
            delays.ToArray());
        var posts = fake.Posts();
        for (int i = 1; i < posts.Count; i++)
        {
            Assert.Equal(posts[0].Query["requestId"], posts[i].Query["requestId"]);
            Assert.Equal("true", posts[i].Query.GetValueOrDefault("retry"));
        }
        Assert.DoesNotContain(FakeSqlApi.Token, ex.Message);
    }

    [Fact]
    public async Task ExecutorNeverRetriesMalformed400()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("400"));
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("result")); // must never be reached
        var (executor, delays) = NewExecutor(fake);

        var ex = await Assert.ThrowsAsync<SnowflakeSqlException>(() =>
            executor.ExecuteAsync(FullStatement));
        Assert.Equal(400, ex.StatusCode);
        Assert.Single(fake.Posts());
        Assert.Empty(delays);
    }

    [Fact]
    public async Task ExecutorNeverRetriesSqlFailure422()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("422"));
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("result")); // must never be reached
        var (executor, delays) = NewExecutor(fake);

        var ex = await Assert.ThrowsAsync<SnowflakeSqlException>(() =>
            executor.ExecuteAsync(FullStatement));
        Assert.Equal("001003", ex.Code);
        Assert.Equal("42000", ex.SqlState);
        Assert.Equal(FakeSqlApi.Handle, ex.StatementHandle);
        Assert.Single(fake.Posts());
        Assert.Empty(delays);
    }

    [Fact]
    public async Task NominallySynchronousQueryReturning202IsPolledNotResubmitted()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("status202"));
        fake.PollPlan.Enqueue(new FakeSqlApi.PollSpec("202"));
        fake.PollPlan.Enqueue(new FakeSqlApi.PollSpec("result"));
        var (executor, delays) = NewExecutor(fake);

        var result = await executor.ExecuteAsync(FullStatement);
        Assert.Equal(FakeSqlApi.Handle, result.StatementHandle);
        Assert.Equal(2, result.NumRows);

        Assert.Single(fake.Posts()); // resubmitting would risk running the DML twice
        var gets = fake.Gets();
        Assert.Equal(2, gets.Count);
        foreach (var get in gets)
        {
            Assert.Equal($"{FakeSqlApi.BasePath}/{FakeSqlApi.Handle}", get.Path);
            CheckCommonHeaders(get, post: false);
        }
        Assert.Equal(new[] { TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(1) }, delays.ToArray());
    }

    [Fact]
    public async Task AmbiguousFailureAfterExecutionDedupesByRequestId()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("exec500"));
        var (executor, delays) = NewExecutor(fake);

        var result = await executor.ExecuteAsync(FullStatement);
        Assert.Equal(FakeSqlApi.Handle, result.StatementHandle);

        var posts = fake.Posts();
        Assert.Equal(2, posts.Count);
        var rid = posts[0].Query["requestId"];
        Assert.Equal(rid, posts[1].Query["requestId"]);
        Assert.Equal("true", posts[1].Query.GetValueOrDefault("retry"));
        Assert.Equal(1, fake.Executions.GetValueOrDefault(rid));
        Assert.Equal(new[] { TimeSpan.FromSeconds(1) }, delays.ToArray());
    }

    [Fact]
    public async Task PollBudgetIsBounded()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("status202"));
        // PollPlan left empty: the fake answers 202 forever
        var (executor, delays) = NewExecutor(fake);

        var ex = await Assert.ThrowsAsync<SnowflakeTransportException>(() =>
            executor.ExecuteAsync(FullStatement));
        Assert.Equal(10, fake.Gets().Count);
        Assert.Single(fake.Posts());
        Assert.Equal(10, delays.Count);
        Assert.DoesNotContain(FakeSqlApi.Token, ex.Message);
    }

    [Fact]
    public async Task TokenNeverLeaksIntoExceptions()
    {
        using var fake = new FakeSqlApi();
        fake.SubmitPlan.Enqueue(new FakeSqlApi.SubmitSpec("401"));
        var (executor, _) = NewExecutor(fake);

        var ex = await Assert.ThrowsAsync<SnowflakeSqlException>(() =>
            executor.ExecuteAsync(FullStatement));
        Assert.Equal(401, ex.StatusCode);
        Assert.DoesNotContain(FakeSqlApi.Token, ex.Message);
        Assert.DoesNotContain(FakeSqlApi.Token, ex.ToString());
    }
}
