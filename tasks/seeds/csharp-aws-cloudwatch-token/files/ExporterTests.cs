// Acceptance tests for the CloudWatch Logs token-paged exporter.
//
// A genuine AmazonCloudWatchLogsClient (AWS SDK for .NET V4) is pointed at a
// loopback fake speaking the documented x-amz-json-1.1 wire protocol pinned
// in docs/contract.json. Dummy credentials, MaxErrorRetry=0, no vendor
// network.

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using Amazon.CloudWatchLogs;
using Amazon.CloudWatchLogs.Model;
using Amazon.Runtime;
using CloudWatchTokenExport;

namespace CloudWatchTokenExportTests;

public sealed class FakeLogsService : IDisposable
{
    public sealed record Recorded(string Target, string Body, string? Auth, string? ContentType);

    public List<Recorded> Requests { get; } = new();
    public string BaseUrl { get; }

    private readonly Queue<Func<string, JsonElement, (int Status, string Body)>> _script = new();
    private readonly HttpListener _listener;
    private readonly Task _loop;

    public FakeLogsService()
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

    public void Enqueue(Func<string, JsonElement, (int Status, string Body)> handler) =>
        _script.Enqueue(handler);

    public JsonElement Request(int index) =>
        JsonDocument.Parse(Requests[index].Body).RootElement;

    public static string StreamsPage(string[] names, string? next)
    {
        var streams = new List<object>();
        long t = 1752600000000;
        foreach (var n in names)
        {
            streams.Add(new Dictionary<string, object> { ["logStreamName"] = n, ["creationTime"] = t++ });
        }
        var body = new Dictionary<string, object> { ["logStreams"] = streams };
        if (next != null) body["nextToken"] = next;
        return JsonSerializer.Serialize(body);
    }

    public static string EventsPage((long Ts, string Msg, long Ing)[] events, string fwd, string bwd = "b/000")
    {
        var list = new List<object>();
        foreach (var e in events)
        {
            list.Add(new Dictionary<string, object>
            {
                ["timestamp"] = e.Ts,
                ["message"] = e.Msg,
                ["ingestionTime"] = e.Ing,
            });
        }
        return JsonSerializer.Serialize(new Dictionary<string, object>
        {
            ["events"] = list,
            ["nextForwardToken"] = fwd,
            ["nextBackwardToken"] = bwd,
        });
    }

    public static (int, string) Error(string type, string message) =>
        (400, JsonSerializer.Serialize(new Dictionary<string, object>
        {
            ["__type"] = type,
            ["message"] = message,
        }));

    private async Task LoopAsync()
    {
        while (_listener.IsListening)
        {
            HttpListenerContext ctx;
            try { ctx = await _listener.GetContextAsync(); }
            catch (Exception) { return; }
            try { Handle(ctx); }
            catch (Exception ex)
            {
                TryRespond(ctx, 500, $"{{\"__type\":\"ServiceUnavailableException\",\"message\":\"mock: {ex.Message}\"}}", null);
            }
        }
    }

    private void Handle(HttpListenerContext ctx)
    {
        string body;
        using (var reader = new StreamReader(ctx.Request.InputStream, Encoding.UTF8))
        {
            body = reader.ReadToEnd();
        }
        var target = ctx.Request.Headers["X-Amz-Target"] ?? "";
        lock (Requests)
        {
            Requests.Add(new Recorded(target, body,
                ctx.Request.Headers["Authorization"], ctx.Request.ContentType));
        }
        Func<string, JsonElement, (int, string)>? handler = null;
        lock (_script)
        {
            if (_script.Count > 0) handler = _script.Dequeue();
        }
        if (handler == null)
        {
            TryRespond(ctx, 400, "{\"__type\":\"InvalidParameterException\",\"message\":\"mock: unscripted call\"}", target);
            return;
        }
        var (status, payload) = handler(target, JsonDocument.Parse(body).RootElement);
        TryRespond(ctx, status, payload, target);
    }

    private static void TryRespond(HttpListenerContext ctx, int status, string payload, string? target)
    {
        try
        {
            byte[] bytes = Encoding.UTF8.GetBytes(payload);
            ctx.Response.StatusCode = status;
            ctx.Response.ContentType = "application/x-amz-json-1.1";
            if (status >= 400)
            {
                var type = JsonDocument.Parse(payload).RootElement.GetProperty("__type").GetString();
                ctx.Response.Headers["x-amzn-ErrorType"] = type;
            }
            ctx.Response.ContentLength64 = bytes.Length;
            ctx.Response.OutputStream.Write(bytes);
            ctx.Response.Close();
        }
        catch (Exception)
        {
            // client hung up; nothing to do
        }
    }

    public void Dispose()
    {
        try { _listener.Stop(); } catch (Exception) { }
        try { _loop.Wait(TimeSpan.FromSeconds(2)); } catch (Exception) { }
    }
}

public sealed class ListSink : ILogSink
{
    public List<(string Stream, List<ExportedEvent> Events)> Appends { get; } = new();
    public Action? OnAppend { get; set; }

    public void Append(string streamName, IReadOnlyList<ExportedEvent> events)
    {
        Appends.Add((streamName, events.ToList()));
        OnAppend?.Invoke();
    }
}

public sealed class ExporterTests : IDisposable
{
    private const string Group = "/ecs/render-fleet";
    private const string AccessKey = "TEST_ACCESS_KEY_ID";
    private const string SecretKey = "dummy-secret-never-real";

    private static readonly JsonElement Contract = JsonDocument.Parse(
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "docs", "contract.json"))).RootElement;

    private readonly FakeLogsService _fake = new();
    private readonly List<TimeSpan> _delays = new();

    private AmazonCloudWatchLogsClient MakeClient()
    {
        var config = new AmazonCloudWatchLogsConfig
        {
            ServiceURL = _fake.BaseUrl,
            AuthenticationRegion = "us-east-1",
            MaxErrorRetry = 0,
        };
        return new AmazonCloudWatchLogsClient(new BasicAWSCredentials(AccessKey, SecretKey), config);
    }

    private LogExporter MakeExporter(int maxRetries = 3, int baseMs = 100, int maxMs = 5000)
    {
        var options = new ExporterOptions
        {
            MaxThrottleRetries = maxRetries,
            BaseDelay = TimeSpan.FromMilliseconds(baseMs),
            MaxDelay = TimeSpan.FromMilliseconds(maxMs),
            DelayAsync = (delay, _) => { _delays.Add(delay); return Task.CompletedTask; },
        };
        return new LogExporter(MakeClient(), options);
    }

    public void Dispose() => _fake.Dispose();

    // ------------------------------------------------------------ contract

    [Fact]
    public void DescribeLimitMatchesDocumentedMaximum()
    {
        int documented = Contract.GetProperty("describe_log_streams").GetProperty("limit_max").GetInt32();
        Assert.Equal(LogExporter.DescribeLimit, documented);
    }

    // ----------------------------------------------------- stream paging

    [Fact]
    public async Task ListStreamNamesFollowsNextTokenUntilAbsent()
    {
        _fake.Enqueue((_, _) => (200, FakeLogsService.StreamsPage(new[] { "svc-a", "svc-b" }, "tok-1")));
        _fake.Enqueue((_, _) => (200, FakeLogsService.StreamsPage(new[] { "svc-c" }, "tok-2")));
        _fake.Enqueue((_, _) => (200, FakeLogsService.StreamsPage(new[] { "svc-d" }, null)));

        var names = await MakeExporter().ListStreamNamesAsync(Group);

        Assert.Equal(new[] { "svc-a", "svc-b", "svc-c", "svc-d" }, names);
        Assert.Equal(3, _fake.Requests.Count);

        string wantTarget = Contract.GetProperty("describe_log_streams").GetProperty("target").GetString()!;
        foreach (var r in _fake.Requests)
        {
            Assert.Equal(wantTarget, r.Target);
            Assert.StartsWith("application/x-amz-json-1.1", r.ContentType);
        }
        var first = _fake.Request(0);
        Assert.Equal(Group, first.GetProperty("logGroupName").GetString());
        Assert.Equal(50, first.GetProperty("limit").GetInt32());
        Assert.Equal("LogStreamName", first.GetProperty("orderBy").GetString());
        Assert.False(first.TryGetProperty("nextToken", out _));

        Assert.Equal("tok-1", _fake.Request(1).GetProperty("nextToken").GetString());
        Assert.Equal("tok-2", _fake.Request(2).GetProperty("nextToken").GetString());
        Assert.Empty(_delays);
    }

    // ------------------------------------------------------ event paging

    [Fact]
    public async Task ReadStreamStopsOnlyOnRepeatedForwardToken()
    {
        _fake.Enqueue((_, _) => (200, FakeLogsService.EventsPage(
            new[] { (100L, "boot", 105L), (200L, "ready", 205L) }, "f/1")));
        // Empty page with a NEW token: pagination must continue.
        _fake.Enqueue((_, _) => (200, FakeLogsService.EventsPage(
            Array.Empty<(long, string, long)>(), "f/2")));
        _fake.Enqueue((_, _) => (200, FakeLogsService.EventsPage(
            new[] { (300L, "shutdown", 305L) }, "f/3")));
        // Token repeats what we sent: the documented end-of-stream signal.
        _fake.Enqueue((_, _) => (200, FakeLogsService.EventsPage(
            Array.Empty<(long, string, long)>(), "f/3")));

        var export = await MakeExporter().ReadStreamAsync(Group, "svc-a", 0, 1000);

        Assert.Equal(4, export.Pages);
        Assert.Equal(new[] { "boot", "ready", "shutdown" }, export.Events.Select(e => e.Message));
        Assert.Equal(new[] { 100L, 200L, 300L }, export.Events.Select(e => e.TimestampMs));
        Assert.Equal(new[] { 105L, 205L, 305L }, export.Events.Select(e => e.IngestionMs));

        string wantTarget = Contract.GetProperty("get_log_events").GetProperty("target").GetString()!;
        Assert.All(_fake.Requests, r => Assert.Equal(wantTarget, r.Target));

        var first = _fake.Request(0);
        Assert.Equal(Group, first.GetProperty("logGroupName").GetString());
        Assert.Equal("svc-a", first.GetProperty("logStreamName").GetString());
        Assert.True(first.GetProperty("startFromHead").GetBoolean());
        Assert.Equal(0, first.GetProperty("startTime").GetInt64());
        Assert.Equal(1000, first.GetProperty("endTime").GetInt64());
        Assert.False(first.TryGetProperty("nextToken", out _));

        Assert.Equal("f/1", _fake.Request(1).GetProperty("nextToken").GetString());
        Assert.True(_fake.Request(1).GetProperty("startFromHead").GetBoolean());
        Assert.Equal("f/2", _fake.Request(2).GetProperty("nextToken").GetString());
        Assert.Equal("f/3", _fake.Request(3).GetProperty("nextToken").GetString());
    }

    // ---------------------------------------------------------- throttling

    [Fact]
    public async Task ThrottlingIsRetriedWithBackoffThenSucceeds()
    {
        _fake.Enqueue((_, _) => FakeLogsService.Error("ThrottlingException", "Rate exceeded"));
        _fake.Enqueue((_, _) => (200, FakeLogsService.StreamsPage(new[] { "svc-a" }, null)));

        var names = await MakeExporter().ListStreamNamesAsync(Group);

        Assert.Equal(new[] { "svc-a" }, names);
        Assert.Equal(2, _fake.Requests.Count);
        Assert.Equal(new[] { TimeSpan.FromMilliseconds(100) }, _delays);
    }

    [Fact]
    public async Task ThrottlingRetriesAreBoundedWithDoublingDelays()
    {
        for (int i = 0; i < 3; i++)
        {
            _fake.Enqueue((_, _) => FakeLogsService.Error("ThrottlingException", "Rate exceeded"));
        }

        var ex = await Assert.ThrowsAsync<ExportException>(
            () => MakeExporter(maxRetries: 2).ListStreamNamesAsync(Group));

        Assert.Equal(LogErrorKind.Throttled, ex.Kind);
        Assert.Equal(Group, ex.LogGroup);
        Assert.Equal(3, _fake.Requests.Count);
        Assert.Equal(new[] { TimeSpan.FromMilliseconds(100), TimeSpan.FromMilliseconds(200) }, _delays);
        var inner = Assert.IsAssignableFrom<AmazonCloudWatchLogsException>(ex.InnerException);
        Assert.Equal("ThrottlingException", inner.ErrorCode);
    }

    [Fact]
    public async Task BackoffDelayIsCappedAtMaxDelay()
    {
        for (int i = 0; i < 4; i++)
        {
            _fake.Enqueue((_, _) => FakeLogsService.Error("ThrottlingException", "Rate exceeded"));
        }

        await Assert.ThrowsAsync<ExportException>(
            () => MakeExporter(maxRetries: 3, baseMs: 100, maxMs: 250).ListStreamNamesAsync(Group));

        Assert.Equal(
            new[] { TimeSpan.FromMilliseconds(100), TimeSpan.FromMilliseconds(200), TimeSpan.FromMilliseconds(250) },
            _delays);
    }

    // ---------------------------------------------------- terminal errors

    [Fact]
    public async Task InvalidParameterIsTerminalWithStreamContext()
    {
        _fake.Enqueue((_, _) => FakeLogsService.Error("InvalidParameterException", "invalid nextToken"));

        var ex = await Assert.ThrowsAsync<ExportException>(
            () => MakeExporter().ReadStreamAsync(Group, "svc-a", 0, 1000));

        Assert.Equal(LogErrorKind.InvalidParameter, ex.Kind);
        Assert.Equal("svc-a", ex.StreamName);
        Assert.Empty(_delays);
        Assert.Single(_fake.Requests);
        Assert.NotNull(ex.InnerException);
    }

    [Fact]
    public async Task LegacySequenceTokenErrorIsTerminalNotThrottling()
    {
        _fake.Enqueue((_, _) => FakeLogsService.Error("InvalidSequenceTokenException", "legacy write-path error"));

        var ex = await Assert.ThrowsAsync<ExportException>(
            () => MakeExporter().ReadStreamAsync(Group, "svc-a", 0, 1000));

        Assert.Equal(LogErrorKind.InvalidSequenceToken, ex.Kind);
        Assert.Empty(_delays);
        Assert.Single(_fake.Requests);
    }

    [Fact]
    public void ClassifierDistinguishesDocumentedErrorFamilies()
    {
        Assert.Equal(LogErrorKind.Throttled,
            LogErrorClassifier.Classify(new AmazonCloudWatchLogsException("x") { ErrorCode = "ThrottlingException" }));
        Assert.Equal(LogErrorKind.Throttled,
            LogErrorClassifier.Classify(new AmazonCloudWatchLogsException("x") { ErrorCode = "LimitExceededException" }));
        Assert.Equal(LogErrorKind.InvalidSequenceToken,
            LogErrorClassifier.Classify(new InvalidSequenceTokenException("x")));
        Assert.Equal(LogErrorKind.InvalidSequenceToken,
            LogErrorClassifier.Classify(new DataAlreadyAcceptedException("x")));
        Assert.Equal(LogErrorKind.InvalidParameter,
            LogErrorClassifier.Classify(new InvalidParameterException("x")));
        Assert.Equal(LogErrorKind.NotFound,
            LogErrorClassifier.Classify(new ResourceNotFoundException("x")));
        Assert.Equal(LogErrorKind.Unknown,
            LogErrorClassifier.Classify(new AmazonCloudWatchLogsException("x") { ErrorCode = "ServiceUnavailableException" }));
    }

    // -------------------------------------------------------- full export

    [Fact]
    public async Task ExportWalksStreamsAndPreservesOrderAndCredentialHygiene()
    {
        _fake.Enqueue((_, _) => (200, FakeLogsService.StreamsPage(new[] { "svc-a", "svc-b" }, null)));
        _fake.Enqueue((_, _) => (200, FakeLogsService.EventsPage(
            new[] { (100L, "a1", 101L), (200L, "a2", 201L) }, "f/a1")));
        _fake.Enqueue((_, _) => (200, FakeLogsService.EventsPage(
            Array.Empty<(long, string, long)>(), "f/a1")));
        _fake.Enqueue((_, _) => (200, FakeLogsService.EventsPage(
            new[] { (150L, "b1", 151L) }, "f/b1")));
        _fake.Enqueue((_, _) => (200, FakeLogsService.EventsPage(
            Array.Empty<(long, string, long)>(), "f/b1")));

        var sink = new ListSink();
        var report = await MakeExporter().ExportAsync(Group, 0, 1000, sink);

        Assert.Equal(2, report.Streams);
        Assert.Equal(3, report.Events);
        Assert.Equal(4, report.Pages);

        Assert.Equal(2, sink.Appends.Count);
        Assert.Equal("svc-a", sink.Appends[0].Stream);
        Assert.Equal(new[] { "a1", "a2" }, sink.Appends[0].Events.Select(e => e.Message));
        Assert.Equal("svc-b", sink.Appends[1].Stream);
        Assert.Equal(new[] { "b1" }, sink.Appends[1].Events.Select(e => e.Message));

        // Dummy credentials must be signed with, never leaked: the SigV4
        // Authorization header carries the access key id but NEVER the secret.
        foreach (var r in _fake.Requests)
        {
            Assert.NotNull(r.Auth);
            Assert.Contains(AccessKey, r.Auth);
            Assert.DoesNotContain(SecretKey, r.Auth);
            Assert.DoesNotContain(SecretKey, r.Body);
        }
    }

    [Fact]
    public async Task ExportHonorsCancellationAndKeepsPartialResults()
    {
        _fake.Enqueue((_, _) => (200, FakeLogsService.StreamsPage(new[] { "svc-a" }, null)));
        _fake.Enqueue((_, _) => (200, FakeLogsService.EventsPage(
            new[] { (100L, "a1", 101L) }, "f/a1")));
        // No further pages scripted: cancellation must stop the walk first.

        using var cts = new CancellationTokenSource();
        var sink = new ListSink { OnAppend = () => cts.Cancel() };

        await Assert.ThrowsAnyAsync<OperationCanceledException>(
            () => MakeExporter().ExportAsync(Group, 0, 1000, sink, cts.Token));

        Assert.Single(sink.Appends);
        Assert.Equal(new[] { "a1" }, sink.Appends[0].Events.Select(e => e.Message));
        Assert.Equal(2, _fake.Requests.Count);
    }
}
