// Acceptance tests for Foundry.Telemetry and the new Splunk HEC sink.
//
// A loopback fake HEC serves the wire contract pinned in docs/contract.json
// (JSON event envelopes, Splunk token auth, HEC response codes, retryable
// statuses). No real Splunk, no credentials, no sleeps — retry waiting is
// injected and recorded. Protected — do not modify this file, the csproj,
// or anything under docs/.

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using Foundry.Telemetry;
using Foundry.Telemetry.Splunk;

namespace Foundry.Telemetry.Tests;

public sealed class MockHec : IDisposable
{
    public sealed record Recorded(string Method, string RawUrl,
        Dictionary<string, string> Headers, byte[] Body);

    public sealed record Scripted(int Status, string Json);

    public static readonly Scripted Success =
        new(200, "{\"text\":\"Success\",\"code\":0}");

    public List<Recorded> Requests { get; } = new();
    public Queue<Scripted> Script { get; } = new();
    public string BaseUrl { get; }

    private readonly HttpListener _listener;

    public MockHec()
    {
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
            using var buffer = new MemoryStream();
            await ctx.Request.InputStream.CopyToAsync(buffer);

            Scripted reply;
            lock (Requests)
            {
                Requests.Add(new Recorded(ctx.Request.HttpMethod,
                    ctx.Request.RawUrl ?? "", headers, buffer.ToArray()));
                reply = Script.Count > 0 ? Script.Dequeue() : Success;
            }
            byte[] payload = Encoding.UTF8.GetBytes(reply.Json);
            ctx.Response.StatusCode = reply.Status;
            ctx.Response.ContentType = "application/json";
            ctx.Response.ContentLength64 = payload.Length;
            await ctx.Response.OutputStream.WriteAsync(payload);
            ctx.Response.Close();
        }
    }

    public void Dispose()
    {
        try { _listener.Stop(); } catch (Exception) { }
    }
}

public class RouterBehaviorTests
{
    private static LogEvent Evt(string level, string message) =>
        new(DateTimeOffset.FromUnixTimeMilliseconds(1770000000000),
            level, message);

    [Fact]
    public void Router_RoutesByMinimumLevel()
    {
        var all = new InMemorySink();
        var errorsOnly = new InMemorySink();
        var router = new LogRouter()
            .Route(all, "debug")
            .Route(errorsOnly, "error");

        router.Publish(Evt("debug", "cache warm"));
        router.Publish(Evt("info", "belt started"));
        router.Publish(Evt("error", "belt jammed"));

        Assert.Equal(3, all.Events.Count);
        Assert.Single(errorsOnly.Events);
        Assert.Equal("belt jammed", errorsOnly.Events[0].Message);
    }

    [Fact]
    public void Router_FlushAllReachesEverySink()
    {
        var a = new InMemorySink();
        var b = new InMemorySink();
        new LogRouter().Route(a, "info").Route(b, "info").FlushAll();
        Assert.Equal(1, a.FlushCount);
        Assert.Equal(1, b.FlushCount);
    }

    [Fact]
    public void Router_RejectsUnknownLevels()
    {
        Assert.Throws<ArgumentException>(
            () => new LogRouter().Route(new InMemorySink(), "loud"));
    }
}

public class SplunkHecSinkTests : IDisposable
{
    private const string Token = "11dummy2-33dd-44ee-55ff-aa66bb77cc88";

    private readonly MockHec _hec = new();
    private readonly List<TimeSpan> _delays = new();

    public void Dispose() => _hec.Dispose();

    private HecSinkOptions Options(int maxBatchBytes = 4096, int maxAttempts = 3) =>
        new(BaseUrl: _hec.BaseUrl,
            Token: Token,
            Host: "press-07",
            Source: "foundry",
            Sourcetype: "foundry:app",
            MaxBatchBytes: maxBatchBytes,
            MaxAttempts: maxAttempts,
            RetryDelay: TimeSpan.FromMilliseconds(200));

    private SplunkHecSink Sink(int maxBatchBytes = 4096, int maxAttempts = 3) =>
        new(Options(maxBatchBytes, maxAttempts), _delays.Add);

    private static LogEvent Evt(string level, string message,
        Dictionary<string, object?>? fields = null) =>
        new(DateTimeOffset.FromUnixTimeMilliseconds(1770000000250),
            level, message, fields);

    private List<MockHec.Recorded> Snapshot()
    {
        lock (_hec.Requests) return new List<MockHec.Recorded>(_hec.Requests);
    }

    private static List<JsonElement> Envelopes(MockHec.Recorded req) =>
        Encoding.UTF8.GetString(req.Body)
            .Split('\n', StringSplitOptions.RemoveEmptyEntries)
            .Select(line => JsonDocument.Parse(line).RootElement)
            .ToList();

    [Fact]
    public void Sink_SendsDocumentedEnvelopes()
    {
        using var sink = Sink();
        sink.Emit(Evt("info", "belt started",
            new Dictionary<string, object?> { ["site"] = "osl", ["line"] = 3 }));
        sink.Emit(Evt("error", "belt jammed"));
        sink.Flush();

        var requests = Snapshot();
        Assert.Single(requests);
        var req = requests[0];
        Assert.Equal("POST", req.Method);
        Assert.Equal("/services/collector/event", req.RawUrl);
        Assert.Equal("Splunk " + Token, req.Headers["Authorization"]);
        Assert.DoesNotContain(Token, req.RawUrl);

        var envelopes = Envelopes(req);
        Assert.Equal(2, envelopes.Count);
        var first = envelopes[0];
        Assert.Equal(1770000000.25, first.GetProperty("time").GetDouble(), 6);
        Assert.Equal("press-07", first.GetProperty("host").GetString());
        Assert.Equal("foundry", first.GetProperty("source").GetString());
        Assert.Equal("foundry:app", first.GetProperty("sourcetype").GetString());
        Assert.Equal("info", first.GetProperty("event").GetProperty("level").GetString());
        Assert.Equal("belt started",
            first.GetProperty("event").GetProperty("message").GetString());
        Assert.Equal("osl", first.GetProperty("fields").GetProperty("site").GetString());
        Assert.Equal(3, first.GetProperty("fields").GetProperty("line").GetInt32());
        Assert.False(first.TryGetProperty("index", out _),
            "no index option set, so no index key");

        var second = envelopes[1];
        Assert.Equal("belt jammed",
            second.GetProperty("event").GetProperty("message").GetString());
        Assert.False(second.TryGetProperty("fields", out _),
            "events without custom fields must not send a fields key");
    }

    [Fact]
    public void Sink_SplitsBatchesByBytes()
    {
        const int limit = 700;
        using var sink = Sink(maxBatchBytes: limit);
        sink.Emit(Evt("info", "m1 " + new string('x', 177)));
        sink.Emit(Evt("info", "m2 " + new string('x', 177)));
        sink.Emit(Evt("info", "m3 " + new string('x', 177))); // pushes over: auto-flush
        sink.Flush();

        var requests = Snapshot();
        Assert.Equal(2, requests.Count);
        Assert.All(requests, r => Assert.True(r.Body.Length <= limit,
            $"batch of {r.Body.Length} bytes exceeds MaxBatchBytes={limit}"));
        var batch1 = Envelopes(requests[0]);
        var batch2 = Envelopes(requests[1]);
        Assert.Equal(2, batch1.Count);
        Assert.Single(batch2);
        Assert.StartsWith("m1",
            batch1[0].GetProperty("event").GetProperty("message").GetString());
        Assert.StartsWith("m2",
            batch1[1].GetProperty("event").GetProperty("message").GetString());
        Assert.StartsWith("m3",
            batch2[0].GetProperty("event").GetProperty("message").GetString());
    }

    [Fact]
    public void Sink_RejectsAnEventLargerThanTheBatchLimit()
    {
        using var sink = Sink(maxBatchBytes: 700);
        Assert.Throws<ArgumentException>(
            () => sink.Emit(Evt("info", new string('x', 900))));
        sink.Flush();
        Assert.Empty(Snapshot());
    }

    [Fact]
    public void Sink_RetriesBusyAndThrottledResponses()
    {
        _hec.Script.Enqueue(new MockHec.Scripted(503,
            "{\"text\":\"Server is busy\",\"code\":9}"));
        _hec.Script.Enqueue(new MockHec.Scripted(429,
            "{\"text\":\"HEC queue is at capacity and cannot process any more requests\",\"code\":26}"));
        _hec.Script.Enqueue(MockHec.Success);

        using var sink = Sink(maxAttempts: 3);
        sink.Emit(Evt("warn", "voltage sag on line 3"));
        sink.Flush();

        var requests = Snapshot();
        Assert.Equal(3, requests.Count);
        Assert.Equal(Encoding.UTF8.GetString(requests[0].Body),
            Encoding.UTF8.GetString(requests[2].Body));
        Assert.Equal(new[]
        {
            TimeSpan.FromMilliseconds(200),
            TimeSpan.FromMilliseconds(200),
        }, _delays);
    }

    [Fact]
    public void Sink_ClientErrorsAreTerminalAndRedacted()
    {
        _hec.Script.Enqueue(new MockHec.Scripted(400,
            "{\"text\":\"Invalid data format\",\"code\":6}"));
        using var sink = Sink(maxAttempts: 3);
        sink.Emit(Evt("info", "ok"));
        var ex = Assert.Throws<HecRequestException>(() => sink.Flush());
        Assert.Equal(400, ex.Status);
        Assert.Equal(6, ex.HecCode);
        Assert.Equal("Invalid data format", ex.HecText);
        Assert.Contains("Invalid data format", ex.Message);
        Assert.DoesNotContain(Token, ex.Message);
        Assert.Single(Snapshot());
        Assert.Empty(_delays);
    }

    [Fact]
    public void Sink_RetryExhaustionSurfacesTheLastResponse()
    {
        _hec.Script.Enqueue(new MockHec.Scripted(503,
            "{\"text\":\"Server is busy\",\"code\":9}"));
        _hec.Script.Enqueue(new MockHec.Scripted(503,
            "{\"text\":\"Server is busy\",\"code\":9}"));
        using var sink = Sink(maxAttempts: 2);
        sink.Emit(Evt("info", "ok"));
        var ex = Assert.Throws<HecRequestException>(() => sink.Flush());
        Assert.Equal(503, ex.Status);
        Assert.Equal(9, ex.HecCode);
        Assert.Equal(2, Snapshot().Count);
        Assert.Single(_delays);
    }

    [Fact]
    public void Sink_FlushAndDisposeAreDeterministic()
    {
        var sink = Sink();
        sink.Flush();
        Assert.Empty(Snapshot());

        sink.Emit(Evt("info", "closing up"));
        sink.Dispose();
        Assert.Single(Snapshot());

        Assert.Throws<ObjectDisposedException>(
            () => sink.Emit(Evt("info", "too late")));
        sink.Dispose(); // double dispose is a no-op
        Assert.Single(Snapshot());
    }

    [Fact]
    public void Options_ToStringNeverLeaksTheToken()
    {
        var text = Options().ToString();
        Assert.NotNull(text);
        Assert.DoesNotContain(Token, text);
        Assert.Contains("press-07", text); // still useful for diagnostics
    }
}
