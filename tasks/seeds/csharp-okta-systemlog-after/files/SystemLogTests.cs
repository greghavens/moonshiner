// Acceptance tests for the Okta System Log checkpoint exporter.
//
// A loopback fake Okta org serves the /api/v1/logs wire contract pinned in
// docs/contract.json (Link-header pagination, opaque after cursors, rate
// limit headers). No real Okta, no credentials, no sleeps — waiting is
// injected and recorded. Protected — do not modify this file, the csproj,
// or anything under docs/.

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using OktaSyslog;

namespace OktaSyslogTests;

public sealed class MockOkta : IDisposable
{
    public sealed record Recorded(string Method, string RawUrl, Dictionary<string, string> Headers);

    public sealed record Scripted(int Status, string? Json = null, Dictionary<string, string>? Headers = null);

    public List<Recorded> Requests { get; } = new();
    public string BaseUrl { get; }

    private readonly Func<int, Recorded, Scripted> _serve;
    private readonly HttpListener _listener;

    public MockOkta(Func<int, Recorded, Scripted> serve)
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
            int n;
            lock (Requests)
            {
                rec = new Recorded(ctx.Request.HttpMethod, ctx.Request.RawUrl ?? "", headers);
                Requests.Add(rec);
                n = Requests.Count - 1;
            }
            Scripted s;
            try { s = _serve(n, rec); }
            catch (Exception) { s = new Scripted(500, "{\"errorSummary\":\"mock script error\"}"); }

            ctx.Response.StatusCode = s.Status;
            foreach (var (k, v) in s.Headers ?? new Dictionary<string, string>())
                ctx.Response.Headers[k] = v;
            byte[] body = Encoding.UTF8.GetBytes(s.Json ?? "");
            if (s.Json is not null)
                ctx.Response.ContentType = "application/json";
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

public sealed class WaitRecorder
{
    public List<TimeSpan> Waits { get; } = new();

    public Task Wait(TimeSpan span)
    {
        Waits.Add(span);
        return Task.CompletedTask;
    }
}

public static class Fx
{
    public const string Token = "00dummySSWSlogt0ken-fixture-4Qz1";
    public const string Since = "2026-07-01T00:00:00Z";
    public const string Filter = "eventType eq \"user.session.start\"";

    public static string Event(string uuid, string published, string eventType = "user.session.start")
        => $$"""
           {
             "uuid": "{{uuid}}",
             "published": "{{published}}",
             "eventType": "{{eventType}}",
             "version": "0",
             "severity": "INFO",
             "displayMessage": "User login to Okta",
             "actor": {
               "id": "00u3gjksoiRGRAZHLSYV",
               "type": "User",
               "alternateId": "grace.ito@example.com",
               "displayName": "Grace Ito"
             },
             "outcome": { "result": "SUCCESS", "reason": null }
           }
           """;

    public static string Page(params string[] events) => "[" + string.Join(",", events) + "]";

    public static string LinkHeaders(string baseUrl, string selfQuery, string? nextQuery)
    {
        string self = $"<{baseUrl}/api/v1/logs?{selfQuery}>; rel=\"self\"";
        return nextQuery is null ? self : self + $", <{baseUrl}/api/v1/logs?{nextQuery}>; rel=\"next\"";
    }

    public const string RateLimited429 = """
        {
          "errorCode": "E0000047",
          "errorSummary": "API call exceeded rate limit due to too many requests.",
          "errorLink": "E0000047",
          "errorId": "oaeQPivGUjND5v78vbYWW047",
          "errorCauses": []
        }
        """;

    public static Dictionary<string, string> RateHeaders(int limit, int remaining, long resetEpoch, string? link = null)
    {
        var h = new Dictionary<string, string>
        {
            ["X-Rate-Limit-Limit"] = limit.ToString(),
            ["X-Rate-Limit-Remaining"] = remaining.ToString(),
            ["X-Rate-Limit-Reset"] = resetEpoch.ToString(),
        };
        if (link is not null) h["Link"] = link;
        return h;
    }

    public static long NowEpoch() => DateTimeOffset.UtcNow.ToUnixTimeSeconds();

    public static Dictionary<string, string> ParseQuery(string rawUrl)
    {
        var q = new Dictionary<string, string>();
        int idx = rawUrl.IndexOf('?');
        if (idx < 0) return q;
        foreach (string pair in rawUrl[(idx + 1)..].Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            int eq = pair.IndexOf('=');
            string k = eq < 0 ? pair : pair[..eq];
            string v = eq < 0 ? "" : pair[(eq + 1)..];
            q[Uri.UnescapeDataString(k)] = Uri.UnescapeDataString(v.Replace('+', ' '));
        }
        return q;
    }

    public static string PathOf(string rawUrl)
    {
        int idx = rawUrl.IndexOf('?');
        return idx < 0 ? rawUrl : rawUrl[..idx];
    }

    public static (SystemLogClient client, HttpClient http) Client(string baseUrl, WaitRecorder waits)
    {
        var http = new HttpClient();
        return (new SystemLogClient(baseUrl, Token, http, waits.Wait), http);
    }
}

public sealed class MemoryCheckpoint
{
    public string? Stored;
    public List<string> Saves { get; } = new();

    public string? Load() => Stored;

    public void Save(string cursor)
    {
        Saves.Add(cursor);
        Stored = cursor;
    }
}

public class ExporterTests
{
    [Fact]
    public async Task FirstSweepSendsDocumentedQueryAndFollowsNextLinkVerbatim()
    {
        // Page 1: two events + next link with shuffled param order and an
        // opaque cursor. Page 2: empty (polling queries always carry next).
        string? p1Next = null, p2Next = null;
        using var okta = new MockOkta((n, req) => n switch
        {
            0 => new MockOkta.Scripted(200,
                Fx.Page(Fx.Event("evt-a1", "2026-07-01T08:00:00.000Z"),
                        Fx.Event("evt-b2", "2026-07-01T08:05:00.000Z")),
                Fx.RateHeaders(60, 59, Fx.NowEpoch() + 60, p1Next)),
            _ => new MockOkta.Scripted(200, Fx.Page(),
                Fx.RateHeaders(60, 58, Fx.NowEpoch() + 60, p2Next)),
        });
        // Next hrefs deliberately reorder params and add a server-side marker
        // param; a client that rebuilds URLs instead of following the link
        // will drop the marker and fail.
        p1Next = Fx.LinkHeaders(okta.BaseUrl,
            "limit=2&sortOrder=ASCENDING&q0=marker",
            "sortOrder=ASCENDING&after=100_page1cur%3D%3D&limit=2&srvMarker=keep1");
        p2Next = Fx.LinkHeaders(okta.BaseUrl,
            "after=100_page1cur%3D%3D&limit=2",
            "sortOrder=ASCENDING&after=100_page2cur%3D%3D&limit=2&srvMarker=keep2");

        var waits = new WaitRecorder();
        var (client, http) = Fx.Client(okta.BaseUrl, waits);
        using var _ = http;
        var store = new MemoryCheckpoint();
        var delivered = new List<LogEvent>();
        var exporter = new SystemLogExporter(client, store.Load, store.Save,
            page => { delivered.AddRange(page); return Task.CompletedTask; });

        SweepReport report = await exporter.RunSweepAsync(Fx.Since, Fx.Filter, limit: 2);

        var reqs = okta.Snapshot();
        Assert.Equal(2, reqs.Length);
        Assert.All(reqs, r => Assert.Equal("GET", r.Method));
        Assert.All(reqs, r => Assert.Equal("/api/v1/logs", Fx.PathOf(r.RawUrl)));
        Assert.All(reqs, r => Assert.Equal("SSWS " + Fx.Token, r.Headers["Authorization"]));
        Assert.All(reqs, r => Assert.Contains("application/json", r.Headers["Accept"]));

        var q = Fx.ParseQuery(reqs[0].RawUrl);
        Assert.Equal(Fx.Since, q["since"]);
        Assert.Equal(Fx.Filter, q["filter"]);
        Assert.Equal("2", q["limit"]);
        Assert.Equal("ASCENDING", q["sortOrder"]);
        Assert.False(q.ContainsKey("after"), "no after param without a checkpoint");
        Assert.False(q.ContainsKey("until"), "a polling sweep must not bound until");

        // The second request must be the next link verbatim, marker included.
        var q2 = Fx.ParseQuery(reqs[1].RawUrl);
        Assert.Equal("100_page1cur==", q2["after"]);
        Assert.Equal("keep1", q2["srvMarker"]);
        Assert.Equal("2", q2["limit"]);

        Assert.Equal(new[] { "evt-a1", "evt-b2" }, delivered.Select(e => e.Uuid).ToArray());
        Assert.Equal("2026-07-01T08:00:00.000Z", delivered[0].Published);
        Assert.Equal("user.session.start", delivered[0].EventType);
        Assert.Equal("INFO", delivered[0].Severity);
        Assert.Equal("grace.ito@example.com", delivered[0].ActorAlternateId);
        Assert.Equal("SUCCESS", delivered[0].OutcomeResult);

        Assert.Equal(new[] { "100_page1cur==" }, store.Saves);
        Assert.Equal(1, report.PagesDelivered);
        Assert.Equal(2, report.EventsDelivered);
        Assert.Equal(0, report.DuplicatesSkipped);
        Assert.Empty(waits.Waits);
    }

    [Fact]
    public async Task BoundaryDuplicatesAreSkippedByUuid()
    {
        string? n1 = null, n2 = null, n3 = null;
        using var okta = new MockOkta((n, req) => n switch
        {
            0 => new MockOkta.Scripted(200,
                Fx.Page(Fx.Event("evt-a1", "2026-07-01T08:00:00.000Z"),
                        Fx.Event("evt-b2", "2026-07-01T08:05:00.000Z")),
                Fx.RateHeaders(60, 59, Fx.NowEpoch() + 60, n1)),
            1 => new MockOkta.Scripted(200,
                Fx.Page(Fx.Event("evt-b2", "2026-07-01T08:05:00.000Z"),
                        Fx.Event("evt-c3", "2026-07-01T08:09:00.000Z")),
                Fx.RateHeaders(60, 58, Fx.NowEpoch() + 60, n2)),
            _ => new MockOkta.Scripted(200, Fx.Page(),
                Fx.RateHeaders(60, 57, Fx.NowEpoch() + 60, n3)),
        });
        n1 = Fx.LinkHeaders(okta.BaseUrl, "limit=2", "after=100_c1%3D%3D&limit=2");
        n2 = Fx.LinkHeaders(okta.BaseUrl, "after=100_c1%3D%3D&limit=2", "after=100_c2%3D%3D&limit=2");
        n3 = Fx.LinkHeaders(okta.BaseUrl, "after=100_c2%3D%3D&limit=2", "after=100_c3%3D%3D&limit=2");

        var waits = new WaitRecorder();
        var (client, http) = Fx.Client(okta.BaseUrl, waits);
        using var _ = http;
        var store = new MemoryCheckpoint();
        var delivered = new List<LogEvent>();
        var exporter = new SystemLogExporter(client, store.Load, store.Save,
            page => { delivered.AddRange(page); return Task.CompletedTask; });

        SweepReport report = await exporter.RunSweepAsync(Fx.Since, Fx.Filter, limit: 2);

        Assert.Equal(new[] { "evt-a1", "evt-b2", "evt-c3" }, delivered.Select(e => e.Uuid).ToArray());
        Assert.Equal(1, report.DuplicatesSkipped);
        Assert.Equal(3, report.EventsDelivered);
        Assert.Equal(2, report.PagesDelivered);
        Assert.Equal(new[] { "100_c1==", "100_c2==" }, store.Saves);
    }

    [Fact]
    public async Task CheckpointAdvancesOnlyAfterFullDeliveryAndResumeUsesStoredCursor()
    {
        string? n1 = null, n2 = null, n3 = null;
        using var okta = new MockOkta((n, req) =>
        {
            var q = Fx.ParseQuery(req.RawUrl);
            // Any request carrying after=100_c1== (the mid-sweep next link in
            // sweep 1, the resume-from-checkpoint URL in sweep 2) serves the
            // page holding evt-d4; the cursor after it serves the empty tail.
            if (q.TryGetValue("after", out string? cur))
            {
                if (cur == "100_c1==")
                    return new MockOkta.Scripted(200,
                        Fx.Page(Fx.Event("evt-d4", "2026-07-01T08:11:00.000Z")),
                        Fx.RateHeaders(60, 55, Fx.NowEpoch() + 60, n2));
                return new MockOkta.Scripted(200, Fx.Page(),
                    Fx.RateHeaders(60, 54, Fx.NowEpoch() + 60, n3));
            }
            return new MockOkta.Scripted(200,
                Fx.Page(Fx.Event("evt-a1", "2026-07-01T08:00:00.000Z")),
                Fx.RateHeaders(60, 56, Fx.NowEpoch() + 60, n1));
        });
        n1 = Fx.LinkHeaders(okta.BaseUrl, "limit=1", "after=100_c1%3D%3D&limit=1&srvMarker=fail");
        n2 = Fx.LinkHeaders(okta.BaseUrl, "after=100_c1%3D%3D&limit=1", "after=100_c2%3D%3D&limit=1");
        n3 = Fx.LinkHeaders(okta.BaseUrl, "after=100_c2%3D%3D&limit=1", "after=100_c3%3D%3D&limit=1");

        var waits = new WaitRecorder();
        var (client, http) = Fx.Client(okta.BaseUrl, waits);
        using var _ = http;
        var store = new MemoryCheckpoint();
        var delivered = new List<LogEvent>();

        // Sweep 1: page 1 delivers, page 2's delivery blows up mid-sink.
        var exporter1 = new SystemLogExporter(client, store.Load, store.Save,
            page =>
            {
                if (page.Any(e => e.Uuid == "evt-d4"))
                    throw new InvalidOperationException("SIEM sink rejected batch");
                delivered.AddRange(page);
                return Task.CompletedTask;
            });
        await Assert.ThrowsAsync<InvalidOperationException>(
            () => exporter1.RunSweepAsync(Fx.Since, Fx.Filter, limit: 1));

        Assert.Equal(new[] { "100_c1==" }, store.Saves);
        Assert.Equal("100_c1==", store.Stored);

        // Sweep 2 resumes from the stored cursor — no refetch of page 1.
        var exporter2 = new SystemLogExporter(client, store.Load, store.Save,
            page => { delivered.AddRange(page); return Task.CompletedTask; });
        SweepReport report = await exporter2.RunSweepAsync(Fx.Since, Fx.Filter, limit: 1);

        var resumed = okta.Snapshot().Skip(2).ToArray();
        var rq = Fx.ParseQuery(resumed[0].RawUrl);
        Assert.Equal("100_c1==", rq["after"]);
        Assert.Equal(Fx.Since, rq["since"]);
        Assert.Equal(Fx.Filter, rq["filter"]);
        Assert.Equal(new[] { "evt-a1", "evt-d4" }, delivered.Select(e => e.Uuid).ToArray());
        Assert.Equal(new[] { "100_c1==", "100_c2==" }, store.Saves);
        Assert.Equal(1, report.EventsDelivered);
    }

    [Fact]
    public async Task RateLimited429RetriesSameUrlAtResetTime()
    {
        string? next = null;
        using var okta = new MockOkta((n, req) => n switch
        {
            0 => new MockOkta.Scripted(429, Fx.RateLimited429,
                Fx.RateHeaders(60, 0, Fx.NowEpoch() + 30)),
            _ => new MockOkta.Scripted(200,
                Fx.Page(Fx.Event("evt-a1", "2026-07-01T08:00:00.000Z")),
                Fx.RateHeaders(60, 59, Fx.NowEpoch() + 60, next)),
        });
        next = Fx.LinkHeaders(okta.BaseUrl, "limit=5", "after=100_c1%3D%3D&limit=5");

        var waits = new WaitRecorder();
        var (client, http) = Fx.Client(okta.BaseUrl, waits);
        using var _ = http;

        LogPage page = await client.FetchPageAsync(
            "/api/v1/logs?since=" + Uri.EscapeDataString(Fx.Since) + "&limit=5&sortOrder=ASCENDING");

        var reqs = okta.Snapshot();
        Assert.Equal(2, reqs.Length);
        Assert.Equal(reqs[0].RawUrl, reqs[1].RawUrl);

        TimeSpan wait = Assert.Single(waits.Waits);
        Assert.InRange(wait.TotalSeconds, 26, 34);

        Assert.Single(page.Events);
        Assert.Equal("100_c1==", page.NextAfter);
        Assert.NotNull(client.LastRateLimit);
        Assert.Equal(60, client.LastRateLimit!.Limit);
        Assert.Equal(59, client.LastRateLimit!.Remaining);
    }

    [Fact]
    public async Task ExhaustedRateLimitRetriesGiveUpWithTypedError()
    {
        long reset = Fx.NowEpoch() + 15;
        using var okta = new MockOkta((n, req) =>
            new MockOkta.Scripted(429, Fx.RateLimited429, Fx.RateHeaders(60, 0, reset)));

        var waits = new WaitRecorder();
        var (client, http) = Fx.Client(okta.BaseUrl, waits);
        using var _ = http;

        var ex = await Assert.ThrowsAsync<OktaRateLimitException>(
            () => client.FetchPageAsync("/api/v1/logs?limit=5"));

        Assert.Equal(3, okta.Snapshot().Length);
        Assert.Equal(2, waits.Waits.Count);
        Assert.Contains("E0000047", ex.Message);
        Assert.DoesNotContain(Fx.Token, ex.Message);
        Assert.Equal(reset, ex.ResetEpochSeconds);
    }

    [Fact]
    public async Task DepletedBudgetGatesTheNextRequestUntilReset()
    {
        string? n1 = null, n2 = null;
        using var okta = new MockOkta((n, req) => n switch
        {
            0 => new MockOkta.Scripted(200,
                Fx.Page(Fx.Event("evt-a1", "2026-07-01T08:00:00.000Z")),
                Fx.RateHeaders(60, 0, Fx.NowEpoch() + 12, n1)),
            _ => new MockOkta.Scripted(200, Fx.Page(),
                Fx.RateHeaders(60, 59, Fx.NowEpoch() + 72, n2)),
        });
        n1 = Fx.LinkHeaders(okta.BaseUrl, "limit=1", "after=100_c1%3D%3D&limit=1");
        n2 = Fx.LinkHeaders(okta.BaseUrl, "after=100_c1%3D%3D&limit=1", "after=100_c2%3D%3D&limit=1");

        var waits = new WaitRecorder();
        var (client, http) = Fx.Client(okta.BaseUrl, waits);
        using var _ = http;
        var store = new MemoryCheckpoint();
        var exporter = new SystemLogExporter(client, store.Load, store.Save,
            _ => Task.CompletedTask);

        await exporter.RunSweepAsync(Fx.Since, Fx.Filter, limit: 1);

        Assert.Equal(2, okta.Snapshot().Length);
        TimeSpan wait = Assert.Single(waits.Waits);
        Assert.InRange(wait.TotalSeconds, 9, 15);
    }
}
