// Acceptance tests for the enterprise audit-log exporter.
//
// Runs a loopback fake GitHub Enterprise Cloud audit-log endpoint speaking
// the wire contract pinned in docs/contract.json. No vendor network, no real
// credentials, no sleeps. Protected — do not modify.

using System.Net;
using System.Net.Sockets;
using System.Text;
using GheAudit;

namespace GheAuditTests;

public sealed class MockServer : IDisposable
{
    public sealed record Recorded(
        string Method, string RawUrl, Dictionary<string, string> Headers);

    public sealed record Scripted(
        int Status, string? Json = null, Dictionary<string, string>? Headers = null);

    public List<Recorded> Requests { get; } = new();
    public string BaseUrl { get; }

    private readonly Func<int, Recorded, Scripted> _serve;
    private readonly HttpListener _listener;

    public MockServer(Func<int, Recorded, Scripted> serve)
    {
        _serve = serve;
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        int port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        BaseUrl = $"http://127.0.0.1:{port}";
        _listener = new HttpListener();
        _listener.Prefixes.Add(BaseUrl + "/");
        _listener.Start();
        _ = Task.Run(LoopAsync);
    }

    private async Task LoopAsync()
    {
        while (_listener.IsListening)
        {
            HttpListenerContext ctx;
            try { ctx = await _listener.GetContextAsync(); }
            catch (Exception) { return; }

            var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string key in ctx.Request.Headers.AllKeys!)
                headers[key!] = ctx.Request.Headers[key]!;
            Recorded rec;
            lock (Requests)
            {
                rec = new Recorded(ctx.Request.HttpMethod, ctx.Request.RawUrl ?? "", headers);
                Requests.Add(rec);
            }
            Scripted s;
            try { s = _serve(Requests.Count - 1, rec); }
            catch (Exception) { s = new Scripted(500, "{\"message\":\"mock script error\"}"); }

            ctx.Response.StatusCode = s.Status;
            foreach (var (k, v) in s.Headers ?? new Dictionary<string, string>())
                ctx.Response.Headers[k] = v;
            byte[] body = Encoding.UTF8.GetBytes(s.Json ?? "");
            if (s.Json is not null)
                ctx.Response.ContentType = "application/json; charset=utf-8";
            ctx.Response.ContentLength64 = body.Length;
            await ctx.Response.OutputStream.WriteAsync(body);
            ctx.Response.Close();
        }
    }

    public Recorded[] Snapshot()
    {
        lock (Requests) return Requests.ToArray();
    }

    public void Dispose()
    {
        try { _listener.Stop(); } catch (Exception) { }
    }
}

public sealed class FakeCheckpointStore : ICheckpointStore
{
    public string? Stored;
    public List<string> Saves { get; } = new();

    public Task<string?> LoadAsync(CancellationToken ct) => Task.FromResult(Stored);

    public Task SaveAsync(string cursor, CancellationToken ct)
    {
        Saves.Add(cursor);
        Stored = cursor;
        return Task.CompletedTask;
    }
}

public class AuditStreamTests
{
    private const string Token = "ghp_dummyAuditExporter7733";
    private const string Enterprise = "machine-shop-holdings";
    private const string AuditPath = "/enterprises/" + Enterprise + "/audit-log";
    private const string CursorA = "MS42OTQwNDA1fDk1OTk";
    private const string CursorB = "MS42OTQwNDA2fDEwMjE";
    private const string ExpectedPhrase = "action%3Arepo.destroy+created%3A%3E%3D2026-06-01";

    private static readonly string[] Qualifiers =
        { "action:repo.destroy", "created:>=2026-06-01" };

    private static string Ev(string id, string action, string actor, long ts) =>
        $"{{\"@timestamp\":{ts},\"action\":\"{action}\",\"actor\":\"{actor}\"," +
        $"\"actor_id\":4407,\"org\":\"machine-shop\",\"org_id\":99120," +
        $"\"_document_id\":\"{id}\",\"created_at\":{ts},\"business\":\"{Enterprise}\"}}";

    private static readonly string Page1 =
        "[" + Ev("Doc_aa11", "repo.destroy", "cdickens", 1784500000123L) + "," +
              Ev("Doc_bb22", "repo.destroy", "ttrellis", 1784500061456L) + "]";
    private static readonly string Page2 =
        "[" + Ev("Doc_cc33", "repo.destroy", "cdickens", 1784500122789L) + "]";

    private static Dictionary<string, string> NextLink(string url) =>
        new() { ["Link"] = $"<{url}>; rel=\"next\"" };

    private static AuditLogClient NewClient(
        MockServer server, List<TimeSpan>? paces = null) =>
        new(server.BaseUrl, Enterprise, Token, new HttpClient(),
            (delay, ct) => { paces?.Add(delay); return Task.CompletedTask; });

    private static AuditQuery Query() => new()
    {
        Phrase = Qualifiers,
        Include = "all",
        Order = "asc",
        PerPage = 100,
    };

    // Standard three-response script: two event pages chained by Link
    // rel="next" cursors, then an empty page with no Link.
    private static Func<int, MockServer.Recorded, MockServer.Scripted> ThreePages(
        Func<string> baseUrl)
    {
        return (n, req) =>
        {
            if (req.RawUrl.Contains("after=" + CursorB))
                return new MockServer.Scripted(200, "[]");
            if (req.RawUrl.Contains("after=" + CursorA))
                return new MockServer.Scripted(200, Page2, NextLink(
                    $"{baseUrl()}{AuditPath}?phrase={ExpectedPhrase}&include=all&order=asc&per_page=100&after={CursorB}"));
            return new MockServer.Scripted(200, Page1, NextLink(
                $"{baseUrl()}{AuditPath}?phrase={ExpectedPhrase}&include=all&order=asc&per_page=100&after={CursorA}"));
        };
    }

    private static Dictionary<string, string> QueryParams(string rawUrl)
    {
        var result = new Dictionary<string, string>();
        int q = rawUrl.IndexOf('?');
        if (q < 0) return result;
        foreach (var pair in rawUrl[(q + 1)..].Split('&'))
        {
            int eq = pair.IndexOf('=');
            result[eq < 0 ? pair : pair[..eq]] = eq < 0 ? "" : pair[(eq + 1)..];
        }
        return result;
    }

    [Fact]
    public async Task FirstRequestPinsPathQueryEncodingAndHeaders()
    {
        MockServer server = null!;
        server = new MockServer(ThreePages(() => server.BaseUrl));
        using var _ = server;
        var events = new List<AuditEvent>();
        await foreach (var e in NewClient(server).StreamAsync(Query()))
            events.Add(e);

        var reqs = server.Snapshot();
        Assert.Equal(3, reqs.Length);
        var first = reqs[0];
        Assert.Equal("GET", first.Method);
        Assert.StartsWith(AuditPath + "?", first.RawUrl);

        var qs = QueryParams(first.RawUrl);
        // Raw phrase segment: qualifiers joined by literal '+', with ':',
        // '>', '=' percent-encoded — exactly the documented search syntax.
        Assert.Equal(ExpectedPhrase, qs["phrase"]);
        Assert.Equal("all", qs["include"]);
        Assert.Equal("asc", qs["order"]);
        Assert.Equal("100", qs["per_page"]);
        Assert.False(qs.ContainsKey("after"), "no after cursor on a fresh export");

        Assert.Equal("application/vnd.github+json", first.Headers["Accept"]);
        Assert.Equal("2026-03-10", first.Headers["X-GitHub-Api-Version"]);
        Assert.Equal("Bearer " + Token, first.Headers["Authorization"]);
        Assert.True(first.Headers.TryGetValue("User-Agent", out var ua) && ua.Length > 0,
            "GitHub requires a User-Agent header");
    }

    [Fact]
    public async Task LinkPaginationIsFollowedVerbatimAndEventsDecoded()
    {
        MockServer server = null!;
        server = new MockServer(ThreePages(() => server.BaseUrl));
        using var _ = server;
        var events = new List<AuditEvent>();
        await foreach (var e in NewClient(server).StreamAsync(Query()))
            events.Add(e);

        var reqs = server.Snapshot();
        Assert.Equal(3, reqs.Length);
        Assert.Contains("after=" + CursorA, reqs[1].RawUrl);
        Assert.Equal(
            $"{AuditPath}?phrase={ExpectedPhrase}&include=all&order=asc&per_page=100&after={CursorA}",
            reqs[1].RawUrl);
        Assert.Contains("after=" + CursorB, reqs[2].RawUrl);

        Assert.Equal(3, events.Count);
        Assert.Equal(new[] { "Doc_aa11", "Doc_bb22", "Doc_cc33" },
            events.Select(e => e.DocumentId).ToArray());
        Assert.Equal("repo.destroy", events[0].Action);
        Assert.Equal("cdickens", events[0].Actor);
        Assert.Equal("machine-shop", events[0].Org);
        Assert.Equal(1784500000123L, events[0].TimestampMs);
        Assert.Equal(1784500061456L, events[1].TimestampMs);
    }

    [Fact]
    public async Task ExporterAdvancesCheckpointOnlyAfterCompletePageSet()
    {
        MockServer server = null!;
        server = new MockServer(ThreePages(() => server.BaseUrl));
        using var _ = server;
        var store = new FakeCheckpointStore();
        var exporter = new AuditExporter(NewClient(server), store);

        var result = await exporter.RunAsync(Query());
        Assert.True(result.Complete);
        Assert.Equal(3, result.Events.Count);
        Assert.Equal(CursorB, result.Checkpoint);
        Assert.Equal(new[] { CursorB }, store.Saves.ToArray());
    }

    [Fact]
    public async Task ResumeUsesStoredAfterCursor()
    {
        MockServer server = null!;
        server = new MockServer(ThreePages(() => server.BaseUrl));
        using var _ = server;
        var store = new FakeCheckpointStore { Stored = CursorB };
        var exporter = new AuditExporter(NewClient(server), store);

        var result = await exporter.RunAsync(Query());
        var reqs = server.Snapshot();
        Assert.Single(reqs);
        Assert.Contains("after=" + CursorB, reqs[0].RawUrl);
        Assert.True(result.Complete);
        Assert.Empty(result.Events);
        Assert.Empty(store.Saves); // nothing new delivered — nothing to save
        Assert.Equal(CursorB, store.Stored);
    }

    [Fact]
    public async Task ServerErrorMidScanKeepsOldCheckpointAndPartialEvents()
    {
        MockServer server = null!;
        server = new MockServer((n, req) =>
        {
            if (req.RawUrl.Contains("after=" + CursorA))
                return new MockServer.Scripted(500, "{\"message\":\"Server Error\"}");
            return new MockServer.Scripted(200, Page1, NextLink(
                $"{server.BaseUrl}{AuditPath}?phrase={ExpectedPhrase}&include=all&order=asc&per_page=100&after={CursorA}"));
        });
        using var _ = server;
        var store = new FakeCheckpointStore();
        var exporter = new AuditExporter(NewClient(server), store);

        var result = await exporter.RunAsync(Query());
        Assert.False(result.Complete);
        Assert.Equal(2, result.Events.Count);
        Assert.Null(result.Checkpoint);
        Assert.Empty(store.Saves);
        var error = Assert.IsType<AuditApiException>(result.Error);
        Assert.Equal(500, error.Status);
    }

    [Fact]
    public async Task RateLimitedPageWaitsRetryAfterThenRetriesOnce()
    {
        var paces = new List<TimeSpan>();
        MockServer server = null!;
        int firstPageCalls = 0;
        server = new MockServer((n, req) =>
        {
            if (req.RawUrl.Contains("after="))
                return new MockServer.Scripted(200, "[]");
            firstPageCalls++;
            if (firstPageCalls == 1)
                return new MockServer.Scripted(429,
                    "{\"message\":\"API rate limit exceeded\"}",
                    new Dictionary<string, string> { ["Retry-After"] = "7" });
            return new MockServer.Scripted(200, Page1, NextLink(
                $"{server.BaseUrl}{AuditPath}?phrase={ExpectedPhrase}&include=all&order=asc&per_page=100&after={CursorA}"));
        });
        using var _ = server;
        var events = new List<AuditEvent>();
        await foreach (var e in NewClient(server, paces).StreamAsync(Query()))
            events.Add(e);

        Assert.Equal(2, events.Count);
        Assert.Equal(new[] { TimeSpan.FromSeconds(7) }, paces.ToArray());
        var reqs = server.Snapshot();
        Assert.Equal(3, reqs.Length);
        Assert.Equal(reqs[0].RawUrl, reqs[1].RawUrl); // identical retry
    }

    [Fact]
    public async Task SecondConsecutiveRateLimitThrowsTyped()
    {
        var paces = new List<TimeSpan>();
        MockServer server = null!;
        server = new MockServer((n, req) => new MockServer.Scripted(429,
            "{\"message\":\"API rate limit exceeded\"}",
            new Dictionary<string, string> { ["Retry-After"] = "11" }));
        using var _ = server;

        var ex = await Assert.ThrowsAsync<AuditRateLimitException>(async () =>
        {
            await foreach (var e in NewClient(server, paces).StreamAsync(Query())) { }
        });
        Assert.Equal(TimeSpan.FromSeconds(11), ex.RetryAfter);
        Assert.Equal(2, server.Snapshot().Length);
        Assert.Single(paces);
        Assert.DoesNotContain(Token, ex.Message);
    }

    [Fact]
    public async Task CrossHostNextLinkIsRefusedWithoutForwardingAuth()
    {
        MockServer server = null!;
        server = new MockServer((n, req) => new MockServer.Scripted(200, Page1,
            NextLink("http://audit-mirror.example:9/enterprises/x/audit-log?after=ZZZ")));
        using var _ = server;

        var ex = await Assert.ThrowsAsync<AuditSecurityException>(async () =>
        {
            await foreach (var e in NewClient(server).StreamAsync(Query())) { }
        });
        Assert.Contains("audit-mirror.example", ex.Message);
        Assert.DoesNotContain(Token, ex.Message);
        Assert.Single(server.Snapshot()); // the off-host URL was never contacted
    }

    [Fact]
    public async Task ClientErrorSurfacesGithubMessageWithoutToken()
    {
        MockServer server = null!;
        server = new MockServer((n, req) => new MockServer.Scripted(403,
            "{\"message\":\"Resource not accessible by personal access token\"," +
            "\"documentation_url\":\"https://docs.github.com/rest\"}"));
        using var _ = server;

        var ex = await Assert.ThrowsAsync<AuditApiException>(async () =>
        {
            await foreach (var e in NewClient(server).StreamAsync(Query())) { }
        });
        Assert.Equal(403, ex.Status);
        Assert.Contains("Resource not accessible", ex.Message);
        Assert.DoesNotContain(Token, ex.Message);
        Assert.DoesNotContain("Bearer", ex.Message);
    }

    [Fact]
    public async Task CancellationStopsBeforeTheNextPageFetch()
    {
        MockServer server = null!;
        server = new MockServer(ThreePages(() => server.BaseUrl));
        using var _ = server;
        using var cts = new CancellationTokenSource();

        var events = new List<AuditEvent>();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(async () =>
        {
            await foreach (var e in NewClient(server).StreamAsync(Query(), cts.Token))
            {
                events.Add(e);
                if (events.Count == 2)
                    cts.Cancel(); // page 1 fully consumed — stop here
            }
        });
        Assert.Equal(2, events.Count);
        Assert.Single(server.Snapshot()); // page 2 must never be requested
    }

    [Fact]
    public async Task DefaultQuerySendsDocumentedDefaults()
    {
        MockServer server = null!;
        server = new MockServer((n, req) => new MockServer.Scripted(200, "[]"));
        using var _ = server;
        await foreach (var e in NewClient(server).StreamAsync(new AuditQuery())) { }

        var qs = QueryParams(server.Snapshot()[0].RawUrl);
        Assert.Equal("web", qs["include"]); // documented default event type
        Assert.Equal("asc", qs["order"]);   // exporter walks forward in time
        Assert.Equal("100", qs["per_page"]);
        Assert.False(qs.ContainsKey("phrase"), "no phrase param when no qualifiers");
    }
}
