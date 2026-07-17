// Acceptance tests for the Oracle Fusion Cloud HCM cancel-work-relationship
// workflow.
//
// A loopback fake Fusion pod serves the wire contract pinned in
// docs/contract.json: worker/relationship ID resolution, the
// application/vnd.oracle.adf.action+json custom action with its
// actionresult reply, the 202-accepted asynchronous variant with
// Location-based status polling, and structured
// application/vnd.oracle.adf.error+json failures. No real tenant, no real
// credentials, no sleeps. Protected — do not modify this file, the csproj,
// .gitignore, or anything under docs/.

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using OracleHcm;

namespace OracleHcmTests;

public sealed class MockPod : IDisposable
{
    public sealed record Recorded(string Method, string RawUrl, Dictionary<string, string> Headers, string Body);

    public sealed record Scripted(int Status, string? Body = null, string? ContentType = null,
        Dictionary<string, string>? Headers = null);

    public List<Recorded> Requests { get; } = new();
    public string Origin { get; }

    private readonly Func<Recorded, Scripted> _serve;
    private readonly HttpListener _listener;

    public MockPod(Func<Recorded, Scripted> serve)
    {
        _serve = serve;
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        int port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        Origin = $"http://127.0.0.1:{port}";
        _listener = new HttpListener();
        _listener.Prefixes.Add(Origin + "/");
        _listener.Start();
        _ = Task.Run(LoopAsync);
    }

    public string BaseUrl => Origin + ActionTests.BasePath;

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
            string body;
            using (var reader = new StreamReader(ctx.Request.InputStream, Encoding.UTF8))
                body = await reader.ReadToEndAsync();

            Recorded rec;
            lock (Requests)
            {
                rec = new Recorded(ctx.Request.HttpMethod, ctx.Request.RawUrl ?? "", headers, body);
                Requests.Add(rec);
            }
            Scripted s;
            try { s = _serve(rec); }
            catch (Exception e) { s = new Scripted(599, e.ToString(), "text/plain"); }

            ctx.Response.StatusCode = s.Status;
            foreach (var (k, v) in s.Headers ?? new Dictionary<string, string>())
                ctx.Response.Headers[k] = v;
            byte[] payload = Encoding.UTF8.GetBytes(s.Body ?? "");
            if (s.Body is not null)
                ctx.Response.ContentType = s.ContentType ?? "application/json";
            ctx.Response.ContentLength64 = payload.Length;
            await ctx.Response.OutputStream.WriteAsync(payload);
            ctx.Response.Close();
        }
    }

    public Recorded[] Snapshot()
    {
        lock (Requests) return Requests.ToArray();
    }

    public void Dispose()
    {
        _listener.Stop();
        _listener.Close();
    }
}

public class ActionTests
{
    public const string BasePath = "/hcmRestApi/resources/11.13.18.05";

    private const string User = "HCM_WF_SVC";
    private const string Pass = "dummy-hcm-secret-93";
    private static readonly string Auth =
        "Basic " + Convert.ToBase64String(Encoding.UTF8.GetBytes($"{User}:{Pass}"));

    private const string WorkersUniqId = "00020000000EACED0005W456";
    private const long PersonId = 300000000000456;
    private const string PersonNumber = "300-88";
    private const long PosSync = 300100999888777;
    private const long PosAsync = 300100999888778;
    private const long PosStuck = 300100999888779;
    private const long PosTerminated = 300100999888780;

    private const string WorkerResolveUrl =
        BasePath + "/workers?q=PersonNumber=%27300-88%27&fields=PersonId,PersonNumber&limit=2";
    private const string MissingResolveUrl =
        BasePath + "/workers?q=PersonNumber=%27999-99%27&fields=PersonId,PersonNumber&limit=2";
    private const string RelationshipResolveUrl =
        BasePath + "/workers/" + WorkersUniqId +
        "/child/workRelationships?q=LegalEmployerName=%27Vertex%20Global%20Services%27&fields=PeriodOfServiceId,LegalEmployerName,WorkerType&onlyData=true";
    private const string ErrorBody = """
        {
          "title": "Bad Request",
          "status": "400",
          "o:errorDetails": [
            {
              "detail": "The work relationship has already been terminated, so it can't be canceled.",
              "o:errorCode": "PER-1531403",
              "o:errorPath": "/ActualTerminationDate"
            },
            {
              "detail": "The action isn't allowed for the worker's current assignment status.",
              "o:errorCode": "27009",
              "o:errorPath": "/actionCode"
            }
          ]
        }
        """;

    private static string ActionUrl(long periodOfServiceId) =>
        $"{BasePath}/workers/{WorkersUniqId}/child/workRelationships/{periodOfServiceId}/action/cancelWorkRelationship";

    private static string WorkerCollectionBody(MockPod pod, int count) =>
        count == 0
            ? """{"items":[],"count":0,"hasMore":false,"limit":2,"offset":0}"""
            : $$"""
              {"items":[{"PersonId":{{PersonId}},"PersonNumber":"{{PersonNumber}}",
                "links":[{"rel":"self","href":"{{pod.Origin}}{{BasePath}}/workers/{{WorkersUniqId}}","name":"workers","kind":"item"}]}],
               "count":1,"hasMore":false,"limit":2,"offset":0}
              """;

    private static string RelationshipCollectionBody(long periodOfServiceId) =>
        $$"""
          {"items":[{"PeriodOfServiceId":{{periodOfServiceId}},"LegalEmployerName":"Vertex Global Services","WorkerType":"E"}],
           "count":1,"hasMore":false,"limit":25,"offset":0}
          """;

    private static MockPod.Scripted NotFound() => new(
        404,
        """{"title":"Not Found","status":"404","o:errorDetails":[]}""",
        "application/vnd.oracle.adf.error+json");

    private static void AssertCommonHeaders(MockPod pod)
    {
        foreach (var rec in pod.Snapshot())
        {
            Assert.Equal(Auth, rec.Headers["Authorization"]);
            Assert.Equal("4", rec.Headers["REST-Framework-Version"]);
            Assert.Equal("application/json", rec.Headers["Accept"]);
        }
    }

    private static HcmActionClient NewClient(MockPod pod, int maxStatusPolls = 5) =>
        new(new HttpClient(), pod.BaseUrl, User, Pass, maxStatusPolls);

    // ------------------------------------------------------------ resolution

    [Fact]
    public async Task ResolvesWorkerByPersonNumber()
    {
        MockPod pod = null!;
        pod = new MockPod(rec => rec.RawUrl == WorkerResolveUrl
            ? new MockPod.Scripted(200, WorkerCollectionBody(pod, 1))
            : NotFound());
        using var podGuard = pod;
        var worker = await NewClient(pod).ResolveWorkerAsync(PersonNumber);

        var reqs = pod.Snapshot();
        Assert.Single(reqs);
        Assert.Equal("GET", reqs[0].Method);
        Assert.Equal(WorkerResolveUrl, reqs[0].RawUrl);
        AssertCommonHeaders(pod);
        Assert.Equal(PersonId, worker.PersonId);
        Assert.Equal(PersonNumber, worker.PersonNumber);
        Assert.Equal(WorkersUniqId, worker.WorkersUniqId);
    }

    [Fact]
    public async Task UnknownPersonNumberFailsResolutionLocally()
    {
        using var pod = new MockPod(rec => rec.RawUrl == MissingResolveUrl
            ? new MockPod.Scripted(200, """{"items":[],"count":0,"hasMore":false,"limit":2,"offset":0}""")
            : NotFound());
        var ex = await Assert.ThrowsAsync<InvalidOperationException>(
            () => NewClient(pod).ResolveWorkerAsync("999-99"));
        Assert.Contains("999-99", ex.Message);
        Assert.Single(pod.Snapshot());
    }

    [Fact]
    public async Task ResolvesWorkRelationshipByLegalEmployer()
    {
        MockPod pod = null!;
        pod = new MockPod(rec => rec.RawUrl switch
        {
            WorkerResolveUrl => new MockPod.Scripted(200, WorkerCollectionBody(pod, 1)),
            RelationshipResolveUrl => new MockPod.Scripted(200, RelationshipCollectionBody(PosSync)),
            _ => NotFound(),
        });
        using var podGuard = pod;
        var client = NewClient(pod);
        var worker = await client.ResolveWorkerAsync(PersonNumber);
        var rel = await client.ResolveRelationshipAsync(worker, "Vertex Global Services");

        var reqs = pod.Snapshot();
        Assert.Equal(2, reqs.Length);
        Assert.Equal(RelationshipResolveUrl, reqs[1].RawUrl);
        AssertCommonHeaders(pod);
        Assert.Equal(PosSync, rel.PeriodOfServiceId);
        Assert.Equal("Vertex Global Services", rel.LegalEmployerName);
        Assert.Equal(WorkersUniqId, rel.Worker.WorkersUniqId);
    }

    // ------------------------------------------------------- synchronous 200

    [Fact]
    public async Task SyncCancelPostsActionAndParsesActionResult()
    {
        MockPod pod = null!;
        pod = new MockPod(rec => rec.RawUrl switch
        {
            WorkerResolveUrl => new MockPod.Scripted(200, WorkerCollectionBody(pod, 1)),
            RelationshipResolveUrl => new MockPod.Scripted(200, RelationshipCollectionBody(PosSync)),
            _ when rec.Method == "POST" && rec.RawUrl == ActionUrl(PosSync) =>
                new MockPod.Scripted(200, """{"returnValue":"true"}""",
                    "application/vnd.oracle.adf.actionresult+json"),
            _ => NotFound(),
        });
        using var podGuard = pod;
        var client = NewClient(pod);
        var worker = await client.ResolveWorkerAsync(PersonNumber);
        var rel = await client.ResolveRelationshipAsync(worker, "Vertex Global Services");
        var outcome = await client.CancelWorkRelationshipAsync(rel, "Termination", "ORA_CANCEL_HIRE");

        var reqs = pod.Snapshot();
        Assert.Equal(3, reqs.Length);
        var post = reqs[2];
        Assert.Equal("POST", post.Method);
        Assert.Equal(ActionUrl(PosSync), post.RawUrl);
        Assert.Equal("application/vnd.oracle.adf.action+json",
            post.Headers["Content-Type"].Split(';')[0].Trim());
        AssertCommonHeaders(pod);

        using var doc = JsonDocument.Parse(post.Body);
        var root = doc.RootElement;
        Assert.Equal(2, root.EnumerateObject().Count());
        Assert.Equal("Termination", root.GetProperty("actionCode").GetString());
        Assert.Equal("ORA_CANCEL_HIRE", root.GetProperty("reasonCode").GetString());

        Assert.Equal("sync", outcome.Mode);
        Assert.Equal(0, outcome.Polls);
        Assert.Equal("true", outcome.ReturnValue);
        Assert.Contains("returnValue", outcome.RawResult);
    }

    // ------------------------------------------------------ asynchronous 202

    [Fact]
    public async Task AsyncCancelFollowsLocationUntilRowIsGone()
    {
        string statusUrl = $"{BasePath}/workers/{WorkersUniqId}/child/workRelationships/{PosAsync}?syncToken=vt-88123";
        int polls = 0;
        MockPod pod = null!;
        pod = new MockPod(rec =>
        {
            if (rec.RawUrl == WorkerResolveUrl)
                return new MockPod.Scripted(200, WorkerCollectionBody(pod, 1));
            if (rec.RawUrl == RelationshipResolveUrl)
                return new MockPod.Scripted(200, RelationshipCollectionBody(PosAsync));
            if (rec.Method == "POST" && rec.RawUrl == ActionUrl(PosAsync))
                return new MockPod.Scripted(202, "", "application/json",
                    new Dictionary<string, string> { ["Location"] = pod.Origin + statusUrl });
            if (rec.Method == "GET" && rec.RawUrl == statusUrl)
            {
                polls++;
                return polls < 3
                    ? new MockPod.Scripted(200, RelationshipCollectionBody(PosAsync))
                    : NotFound();
            }
            return NotFound();
        });
        using var podGuard = pod;
        var client = NewClient(pod);
        var worker = await client.ResolveWorkerAsync(PersonNumber);
        var rel = await client.ResolveRelationshipAsync(worker, "Vertex Global Services");
        var outcome = await client.CancelWorkRelationshipAsync(rel, "Termination", "ORA_CANCEL_HIRE");

        Assert.Equal("async", outcome.Mode);
        Assert.Equal(3, outcome.Polls);
        var reqs = pod.Snapshot();
        Assert.Equal(1, reqs.Count(r => r.Method == "POST"));
        Assert.Equal(3, reqs.Count(r => r.Method == "GET" && r.RawUrl == statusUrl));
        AssertCommonHeaders(pod);
    }

    [Fact]
    public async Task AsyncCancelPollingIsBounded()
    {
        string statusUrl = $"{BasePath}/workers/{WorkersUniqId}/child/workRelationships/{PosStuck}?syncToken=vt-88124";
        MockPod pod = null!;
        pod = new MockPod(rec =>
        {
            if (rec.RawUrl == WorkerResolveUrl)
                return new MockPod.Scripted(200, WorkerCollectionBody(pod, 1));
            if (rec.RawUrl == RelationshipResolveUrl)
                return new MockPod.Scripted(200, RelationshipCollectionBody(PosStuck));
            if (rec.Method == "POST" && rec.RawUrl == ActionUrl(PosStuck))
                return new MockPod.Scripted(202, "", "application/json",
                    new Dictionary<string, string> { ["Location"] = pod.Origin + statusUrl });
            if (rec.Method == "GET" && rec.RawUrl == statusUrl)
                return new MockPod.Scripted(200, RelationshipCollectionBody(PosStuck));
            return NotFound();
        });
        using var podGuard = pod;
        var client = NewClient(pod, maxStatusPolls: 5);
        var worker = await client.ResolveWorkerAsync(PersonNumber);
        var rel = await client.ResolveRelationshipAsync(worker, "Vertex Global Services");
        var ex = await Assert.ThrowsAsync<HcmAsyncTimeoutException>(
            () => client.CancelWorkRelationshipAsync(rel, "Termination", "ORA_CANCEL_HIRE"));

        Assert.Equal(5, ex.Polls);
        var reqs = pod.Snapshot();
        Assert.Equal(1, reqs.Count(r => r.Method == "POST"));
        Assert.Equal(5, reqs.Count(r => r.Method == "GET" && r.RawUrl == statusUrl));
    }

    // ------------------------------------------------------- structured error

    [Fact]
    public async Task CancelErrorPreservesOracleErrorDocument()
    {
        MockPod pod = null!;
        pod = new MockPod(rec => rec.RawUrl switch
        {
            WorkerResolveUrl => new MockPod.Scripted(200, WorkerCollectionBody(pod, 1)),
            RelationshipResolveUrl => new MockPod.Scripted(200, RelationshipCollectionBody(PosTerminated)),
            _ when rec.Method == "POST" && rec.RawUrl == ActionUrl(PosTerminated) =>
                new MockPod.Scripted(400, ErrorBody, "application/vnd.oracle.adf.error+json"),
            _ => NotFound(),
        });
        using var podGuard = pod;
        var client = NewClient(pod);
        var worker = await client.ResolveWorkerAsync(PersonNumber);
        var rel = await client.ResolveRelationshipAsync(worker, "Vertex Global Services");
        var ex = await Assert.ThrowsAsync<OracleRestException>(
            () => client.CancelWorkRelationshipAsync(rel, "Termination", "ORA_CANCEL_HIRE"));

        Assert.Equal(400, ex.HttpStatus);
        Assert.Equal("Bad Request", ex.Title);
        Assert.Equal("400", ex.ErrorStatus);
        Assert.Equal(2, ex.Details.Count);
        Assert.Equal("PER-1531403", ex.Details[0].ErrorCode);
        Assert.Equal("/ActualTerminationDate", ex.Details[0].ErrorPath);
        Assert.Contains("already been terminated", ex.Details[0].Detail);
        Assert.Equal("27009", ex.Details[1].ErrorCode);
        Assert.Equal("/actionCode", ex.Details[1].ErrorPath);
        Assert.Contains("Bad Request", ex.Message);
        Assert.Contains("PER-1531403", ex.Message);
        Assert.DoesNotContain(Pass, ex.Message + ex.ToString());
        Assert.DoesNotContain(Auth.Substring("Basic ".Length), ex.Message + ex.ToString());
        Assert.Equal(1, pod.Snapshot().Count(r => r.Method == "POST"));
    }

    // ------------------------------------------- documented status semantics

    [Theory]
    [InlineData(200, HcmResponseKind.Ok)]
    [InlineData(201, HcmResponseKind.Created)]
    [InlineData(202, HcmResponseKind.Accepted)]
    [InlineData(204, HcmResponseKind.NoContent)]
    [InlineData(400, HcmResponseKind.ClientError)]
    [InlineData(401, HcmResponseKind.ClientError)]
    [InlineData(403, HcmResponseKind.ClientError)]
    [InlineData(404, HcmResponseKind.ClientError)]
    [InlineData(409, HcmResponseKind.ClientError)]
    [InlineData(412, HcmResponseKind.ClientError)]
    [InlineData(428, HcmResponseKind.ClientError)]
    [InlineData(500, HcmResponseKind.ServerError)]
    [InlineData(503, HcmResponseKind.ServerError)]
    public void ClassifierMapsDocumentedStatusCodes(int status, HcmResponseKind kind)
    {
        Assert.Equal(kind, HcmStatusClassifier.Classify(status));
    }
}
