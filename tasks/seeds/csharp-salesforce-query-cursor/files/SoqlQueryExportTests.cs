// Acceptance tests for the Salesforce SOQL query-cursor exporter.
//
// Runs a loopback fake of the Salesforce REST query resource implementing the
// contract pinned in docs/contract.json: q-encoded SOQL, opaque nextRecordsUrl
// cursor pages until done, attributes metadata, the REST error envelope,
// 401 INVALID_SESSION_ID refresh and 503 Retry-After retries.
// No vendor network, no real credentials.

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using SoqlExport;

namespace SoqlExportTests;

public sealed class FakeSalesforceOrg : IDisposable
{
    public const string Token1 = "00Dxx-dummy-session-a41c9e"; // dummy; must never leak
    public const string Token2 = "00Dxx-dummy-session-b52d0f"; // dummy; must never leak

    public const string QueryPath = "/services/data/v67.0/query";
    public const string Next1 = "/services/data/v67.0/query/01gKB0000016PIAYA2-2000";
    public const string Next2 = "/services/data/v67.0/query/01gKB0000016PIAYA2-4000";

    public sealed record Recorded(string Method, string PathAndQuery, string? DecodedQ,
        string? Auth, string? Accept);

    public sealed record Fault(int Status, int RetryAfter, string ErrorCode, string Message);

    public List<Recorded> Requests { get; } = new();
    public string BaseUrl { get; }
    public string? ExpectedSoql { get; set; }
    public int ExpireTokenAfterSuccesses { get; set; } = -1;

    private readonly HashSet<string> _validTokens = new() { Token1 };
    private readonly Dictionary<string, string> _pages = new();
    private readonly Dictionary<string, Queue<Fault>> _faults = new();
    private readonly HttpListener _listener;
    private readonly Task _loop;
    private int _successes;

    public FakeSalesforceOrg()
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

    public void InvalidateAllTokens() => _validTokens.Clear();

    public void SeedStandardPages()
    {
        ExpectedSoql = TestData.Soql;
        _pages["start"] = TestData.Page(5, false, Next1,
            TestData.Acct("001KB000001aaaAAA", "Acme HQ"),
            TestData.Acct("001KB000002bbbAAA", "Blue Harbor Logistics"));
        _pages[Next1] = TestData.Page(5, false, Next2,
            TestData.Acct("001KB000003cccAAA", "Cinder Ridge Mining"),
            TestData.Acct("001KB000004dddAAA", "Dockside Ops"));
        _pages[Next2] = TestData.Page(5, true, null,
            TestData.Acct("001KB000005eeeAAA", "Ember Analytics"));
    }

    public void QueueFault(string pageKey, Fault fault)
    {
        if (!_faults.TryGetValue(pageKey, out var q))
            _faults[pageKey] = q = new Queue<Fault>();
        q.Enqueue(fault);
    }

    public int CountRequests(string pathAndQueryPrefix) =>
        Requests.Count(r => r.PathAndQuery.StartsWith(pathAndQueryPrefix, StringComparison.Ordinal));

    private async Task LoopAsync()
    {
        while (_listener.IsListening)
        {
            HttpListenerContext ctx;
            try { ctx = await _listener.GetContextAsync(); }
            catch (Exception) { return; }
            try { Handle(ctx); }
            catch (Exception) { /* keep serving */ }
        }
    }

    private void Handle(HttpListenerContext ctx)
    {
        var req = ctx.Request;
        string pathAndQuery = req.Url!.PathAndQuery;
        string path = req.Url!.AbsolutePath;
        string? decodedQ = req.QueryString["q"];
        lock (Requests)
        {
            Requests.Add(new Recorded(req.HttpMethod, pathAndQuery, decodedQ,
                req.Headers["Authorization"], req.Headers["Accept"]));
        }

        string? auth = req.Headers["Authorization"];
        bool authorized = auth != null && auth.StartsWith("Bearer ", StringComparison.Ordinal)
            && _validTokens.Contains(auth["Bearer ".Length..]);
        if (!authorized)
        {
            SendJson(ctx, 401, Envelope("INVALID_SESSION_ID", "Session expired or invalid"));
            return;
        }

        string? pageKey = null;
        if (path == QueryPath && decodedQ != null)
        {
            if (ExpectedSoql != null && decodedQ != ExpectedSoql)
            {
                SendJson(ctx, 400, Envelope("MALFORMED_QUERY",
                    $"unexpected SOQL after decoding: {decodedQ}"));
                return;
            }
            pageKey = "start";
        }
        else if (_pages.ContainsKey(pathAndQuery))
        {
            // cursor pages are opaque: only the verbatim URL matches
            pageKey = pathAndQuery;
        }

        if (pageKey == null)
        {
            SendJson(ctx, 404, Envelope("NOT_FOUND",
                $"The requested resource does not exist: {pathAndQuery}"));
            return;
        }

        if (_faults.TryGetValue(pageKey, out var q) && q.Count > 0)
        {
            var fault = q.Dequeue();
            if (fault.RetryAfter > 0)
                ctx.Response.AddHeader("Retry-After", fault.RetryAfter.ToString());
            SendJson(ctx, fault.Status, Envelope(fault.ErrorCode, fault.Message));
            return;
        }

        _successes++;
        if (ExpireTokenAfterSuccesses >= 0 && _successes == ExpireTokenAfterSuccesses)
        {
            _validTokens.Remove(Token1);
            _validTokens.Add(Token2);
        }
        SendJson(ctx, 200, _pages[pageKey]);
    }

    private static string Envelope(string errorCode, string message) =>
        JsonSerializer.Serialize(new[] { new { message, errorCode } });

    private static void SendJson(HttpListenerContext ctx, int status, string body)
    {
        byte[] payload = Encoding.UTF8.GetBytes(body);
        ctx.Response.StatusCode = status;
        ctx.Response.ContentType = "application/json";
        ctx.Response.ContentLength64 = payload.Length;
        ctx.Response.OutputStream.Write(payload);
        ctx.Response.Close();
    }

    public void Dispose()
    {
        try { _listener.Stop(); } catch (Exception) { }
        try { _loop.Wait(TimeSpan.FromSeconds(2)); } catch (Exception) { }
    }
}

public static class TestData
{
    public const string Soql =
        "SELECT Id, Name FROM Account WHERE Industry = 'Energy' ORDER BY Name";

    public static object Acct(string id, string name) => new
    {
        attributes = new
        {
            type = "Account",
            url = $"/services/data/v67.0/sobjects/Account/{id}",
        },
        Id = id,
        Name = name,
    };

    public static string Page(int totalSize, bool done, string? nextRecordsUrl,
        params object[] records)
    {
        var page = new Dictionary<string, object?>
        {
            ["totalSize"] = totalSize,
            ["done"] = done,
            ["records"] = records,
        };
        if (nextRecordsUrl != null) page["nextRecordsUrl"] = nextRecordsUrl;
        return JsonSerializer.Serialize(page);
    }
}

public sealed class FakeSession : ISessionSource
{
    private string _current;
    private readonly string? _next;

    public int RefreshCalls { get; private set; }

    public FakeSession(string current, string? next = null)
    {
        _current = current;
        _next = next;
    }

    public Task<string> GetTokenAsync() => Task.FromResult(_current);

    public Task<string> RefreshTokenAsync()
    {
        RefreshCalls++;
        _current = _next ?? throw new InvalidOperationException("no refreshed token");
        return Task.FromResult(_current);
    }
}

public sealed class DelayRecorder
{
    public List<TimeSpan> Delays { get; } = new();

    public Task Wait(TimeSpan span)
    {
        Delays.Add(span);
        return Task.CompletedTask;
    }
}

public class SoqlQueryExportTests
{
    private static SoqlExporter Exporter(FakeSalesforceOrg org, FakeSession session,
        DelayRecorder? delays = null) =>
        new(org.BaseUrl, "v67.0", session, (delays ?? new DelayRecorder()).Wait);

    [Fact]
    public async Task FullScanFollowsTheCursorContract()
    {
        using var org = new FakeSalesforceOrg();
        org.SeedStandardPages();
        var session = new FakeSession(FakeSalesforceOrg.Token1);

        var export = await Exporter(org, session).RunAsync(TestData.Soql);

        Assert.Equal(3, org.Requests.Count);
        Assert.All(org.Requests, r => Assert.Equal("GET", r.Method));
        Assert.All(org.Requests, r =>
            Assert.Equal($"Bearer {FakeSalesforceOrg.Token1}", r.Auth));
        Assert.All(org.Requests, r =>
            Assert.StartsWith("application/json", r.Accept ?? ""));

        Assert.StartsWith(FakeSalesforceOrg.QueryPath + "?", org.Requests[0].PathAndQuery);
        Assert.Equal(TestData.Soql, org.Requests[0].DecodedQ);
        Assert.Equal(FakeSalesforceOrg.Next1, org.Requests[1].PathAndQuery);
        Assert.Equal(FakeSalesforceOrg.Next2, org.Requests[2].PathAndQuery);

        Assert.Equal(5, export.TotalSize);
        Assert.Equal(3, export.Pages);
        Assert.False(export.SessionRefreshed);
        Assert.Equal(5, export.Records.Count);
        Assert.Equal(
            new[]
            {
                "Acme HQ", "Blue Harbor Logistics", "Cinder Ridge Mining",
                "Dockside Ops", "Ember Analytics",
            },
            export.Records.Select(r => r.Fields["Name"]).ToArray());
        Assert.Equal(5, export.Records.Select(r => r.Id).Distinct().Count());
        Assert.All(export.Records, r => Assert.Equal("Account", r.Type));
        Assert.Equal("/services/data/v67.0/sobjects/Account/001KB000001aaaAAA",
            export.Records[0].Url);
        Assert.Equal("001KB000001aaaAAA", export.Records[0].Id);
    }

    [Fact]
    public async Task ExpiredSessionRefreshesOnceMidScan()
    {
        using var org = new FakeSalesforceOrg();
        org.SeedStandardPages();
        org.ExpireTokenAfterSuccesses = 2;
        var session = new FakeSession(FakeSalesforceOrg.Token1, FakeSalesforceOrg.Token2);

        var export = await Exporter(org, session).RunAsync(TestData.Soql);

        Assert.Equal(1, session.RefreshCalls);
        Assert.True(export.SessionRefreshed);
        Assert.Equal(4, org.Requests.Count);
        Assert.Equal(FakeSalesforceOrg.Next2, org.Requests[2].PathAndQuery);
        Assert.Equal(FakeSalesforceOrg.Next2, org.Requests[3].PathAndQuery);
        Assert.Equal($"Bearer {FakeSalesforceOrg.Token1}", org.Requests[2].Auth);
        Assert.Equal($"Bearer {FakeSalesforceOrg.Token2}", org.Requests[3].Auth);
        Assert.Equal(5, export.Records.Count);
        Assert.Equal(5, export.Records.Select(r => r.Id).Distinct().Count());
    }

    [Fact]
    public async Task SecondAuthFailureRaisesSessionException()
    {
        using var org = new FakeSalesforceOrg();
        org.SeedStandardPages();
        org.InvalidateAllTokens();
        var session = new FakeSession(FakeSalesforceOrg.Token1, FakeSalesforceOrg.Token2);

        var ex = await Assert.ThrowsAsync<SalesforceSessionException>(
            () => Exporter(org, session).RunAsync(TestData.Soql));

        Assert.Equal(401, ex.StatusCode);
        Assert.Equal("INVALID_SESSION_ID", ex.ErrorCode);
        Assert.Equal(1, session.RefreshCalls);
        Assert.Equal(2, org.Requests.Count);
        Assert.DoesNotContain(FakeSalesforceOrg.Token1, ex.ToString());
        Assert.DoesNotContain(FakeSalesforceOrg.Token2, ex.ToString());
    }

    [Fact]
    public async Task ThrottledPageRetriesOnceWithoutDuplicates()
    {
        using var org = new FakeSalesforceOrg();
        org.SeedStandardPages();
        org.QueueFault(FakeSalesforceOrg.Next1, new FakeSalesforceOrg.Fault(
            503, 7, "SERVER_UNAVAILABLE", "The server is unavailable, try again later."));
        var session = new FakeSession(FakeSalesforceOrg.Token1);
        var delays = new DelayRecorder();

        var export = await Exporter(org, session, delays).RunAsync(TestData.Soql);

        Assert.Equal(new[] { TimeSpan.FromSeconds(7) }, delays.Delays);
        Assert.Equal(2, org.CountRequests(FakeSalesforceOrg.Next1));
        Assert.Equal(4, org.Requests.Count);
        Assert.Equal(5, export.Records.Count);
        Assert.Equal(5, export.Records.Select(r => r.Id).Distinct().Count());
        Assert.Equal(3, export.Pages);
    }

    [Fact]
    public async Task PersistentThrottleGivesUpAfterOneRetry()
    {
        using var org = new FakeSalesforceOrg();
        org.SeedStandardPages();
        org.QueueFault(FakeSalesforceOrg.Next1, new FakeSalesforceOrg.Fault(
            503, 3, "SERVER_UNAVAILABLE", "Still unavailable."));
        org.QueueFault(FakeSalesforceOrg.Next1, new FakeSalesforceOrg.Fault(
            503, 3, "SERVER_UNAVAILABLE", "Still unavailable."));
        var session = new FakeSession(FakeSalesforceOrg.Token1);
        var delays = new DelayRecorder();

        var ex = await Assert.ThrowsAsync<SalesforceApiException>(
            () => Exporter(org, session, delays).RunAsync(TestData.Soql));

        Assert.Equal(503, ex.StatusCode);
        Assert.Equal("SERVER_UNAVAILABLE", ex.ErrorCode);
        Assert.Single(delays.Delays);
        Assert.Equal(2, org.CountRequests(FakeSalesforceOrg.Next1));
    }

    [Fact]
    public async Task MalformedQueryFailsFastWithoutRetry()
    {
        using var org = new FakeSalesforceOrg();
        org.SeedStandardPages();
        org.QueueFault("start", new FakeSalesforceOrg.Fault(
            400, 0, "MALFORMED_QUERY", "unexpected token: SELEKT"));
        var session = new FakeSession(FakeSalesforceOrg.Token1);
        var delays = new DelayRecorder();

        var ex = await Assert.ThrowsAsync<SalesforceApiException>(
            () => Exporter(org, session, delays).RunAsync(TestData.Soql));

        Assert.Equal(400, ex.StatusCode);
        Assert.Equal("MALFORMED_QUERY", ex.ErrorCode);
        Assert.Contains("SELEKT", ex.Message);
        Assert.Empty(delays.Delays);
        Assert.Single(org.Requests);
        Assert.DoesNotContain(FakeSalesforceOrg.Token1, ex.ToString());
    }

    [Fact]
    public void FixturesPinTheResearchedContract()
    {
        using var contract = JsonDocument.Parse(File.ReadAllText(
            Path.Combine("docs", "contract.json")));
        using var sources = JsonDocument.Parse(File.ReadAllText(
            Path.Combine("docs", "official_sources.json")));

        Assert.Equal("v67.0", contract.RootElement.GetProperty("api_version").GetString());
        Assert.Equal("INVALID_SESSION_ID", contract.RootElement
            .GetProperty("error_envelope").GetProperty("example_401")
            .GetProperty("errorCode").GetString());
        string cursorRules = contract.RootElement.GetProperty("cursor_rules").ToString();
        Assert.Contains("nextRecordsUrl", cursorRules);

        var research = sources.RootElement.GetProperty("research");
        Assert.True(research.GetProperty("required").GetBoolean());
        var urls = research.GetProperty("official_sources").EnumerateArray()
            .Select(s => s.GetProperty("url").GetString() ?? "").ToList();
        Assert.True(urls.Count >= 2, "at least two official sources");
        Assert.All(urls, u =>
            Assert.StartsWith("https://developer.salesforce.com/", u));
    }
}
