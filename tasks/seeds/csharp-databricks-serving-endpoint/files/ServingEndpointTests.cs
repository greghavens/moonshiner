// Acceptance tests for the DbxServing model-serving endpoint deployer.
//
// A loopback fake Databricks workspace serves the serving-endpoints wire
// contract pinned in docs/contract.json (create, get, update-config,
// readiness/config-update states, per-entity deployment states). No real
// Databricks, no credentials, no sleeps — waiting is injected and recorded.
// Protected — do not modify this file, the csproj, or anything under docs/.

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using DbxServing;

namespace DbxServingTests;

public sealed class MockDatabricks : IDisposable
{
    public sealed record Recorded(string Method, string RawUrl, Dictionary<string, string> Headers, string Body);

    public sealed record Scripted(int Status, string? Json = null);

    public List<Recorded> Requests { get; } = new();
    public string BaseUrl { get; }

    private readonly Func<int, Recorded, Scripted> _serve;
    private readonly HttpListener _listener;

    public MockDatabricks(Func<int, Recorded, Scripted> serve)
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
            string body;
            using (var reader = new StreamReader(ctx.Request.InputStream, Encoding.UTF8))
                body = await reader.ReadToEndAsync();
            Recorded rec;
            int n;
            lock (Requests)
            {
                rec = new Recorded(ctx.Request.HttpMethod, ctx.Request.RawUrl ?? "", headers, body);
                Requests.Add(rec);
                n = Requests.Count - 1;
            }
            Scripted s;
            try { s = _serve(n, rec); }
            catch (Exception) { s = new Scripted(500, "{\"error_code\":\"MOCK_SCRIPT_ERROR\",\"message\":\"mock script error\"}"); }

            ctx.Response.StatusCode = s.Status;
            byte[] payload = Encoding.UTF8.GetBytes(s.Json ?? "");
            if (s.Json is not null)
                ctx.Response.ContentType = "application/json";
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
        try { _listener.Stop(); } catch (Exception) { }
    }
}

public sealed class WaitRecorder
{
    public List<TimeSpan> Waits { get; } = new();
    public Action<int>? OnWait { get; set; }

    public Task Wait(TimeSpan span, CancellationToken ct)
    {
        Waits.Add(span);
        OnWait?.Invoke(Waits.Count);
        return Task.CompletedTask;
    }
}

public static class Fx
{
    public const string Token = "dapi5fixture8e02d47cba1f9636dummy"; // dummy; must never leak
    public const string Name = "churn-scoring";
    public static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(10);
    public const int MaxPolls = 5;

    public static EndpointSpec Spec() => new(
        Name,
        new[]
        {
            new ServedEntitySpec("churn-a", "ml.prod.churn_model", "4", "Small", true),
            new ServedEntitySpec("churn-b", "ml.prod.churn_model", "5", "Small", true),
        },
        new[] { new RouteSpec("churn-a", 80), new RouteSpec("churn-b", 20) });

    public const string NotFound =
        "{\"error_code\": \"RESOURCE_DOES_NOT_EXIST\", \"message\": \"Endpoint with name 'churn-scoring' does not exist.\"}";

    public static string Entity(string name, string version, string deployment, string message = "") =>
        "{\"name\": \"" + name + "\", \"entity_name\": \"ml.prod.churn_model\", \"entity_version\": \"" + version + "\"," +
        " \"workload_size\": \"Small\", \"scale_to_zero_enabled\": true," +
        " \"state\": {\"deployment\": \"" + deployment + "\", \"deployment_state_message\": \"" + message + "\"}}";

    public const string RoutesJson =
        "{\"routes\": [{\"served_model_name\": \"churn-a\", \"traffic_percentage\": 80}," +
        " {\"served_model_name\": \"churn-b\", \"traffic_percentage\": 20}]}";

    public static string Deploying(string entA = "DEPLOYMENT_CREATING", string entB = "DEPLOYMENT_CREATING",
        string configUpdate = "IN_PROGRESS", string msgB = "") =>
        "{\"name\": \"" + Name + "\", \"creation_timestamp\": 1752700000000, \"last_updated_timestamp\": 1752700000000," +
        " \"state\": {\"ready\": \"NOT_READY\", \"config_update\": \"" + configUpdate + "\"}," +
        " \"pending_config\": {\"start_time\": 1752700005000, \"config_version\": 1," +
        " \"served_entities\": [" + Entity("churn-a", "4", entA) + ", " + Entity("churn-b", "5", entB, msgB) + "]," +
        " \"traffic_config\": " + RoutesJson + "}}";

    public static string Ready() =>
        "{\"name\": \"" + Name + "\", \"creation_timestamp\": 1752700000000, \"last_updated_timestamp\": 1752700900000," +
        " \"state\": {\"ready\": \"READY\", \"config_update\": \"NOT_UPDATING\"}," +
        " \"endpoint_url\": \"https://fixture.example/serving-endpoints/churn-scoring/invocations\"," +
        " \"config\": {\"config_version\": 1," +
        " \"served_entities\": [" + Entity("churn-a", "4", "DEPLOYMENT_READY") + ", " + Entity("churn-b", "5", "DEPLOYMENT_READY") + "]," +
        " \"traffic_config\": " + RoutesJson + "}}";

    public static EndpointDeployer Deployer(MockDatabricks mock, WaitRecorder waits, int maxPolls = MaxPolls) =>
        new(new ServingClient(new HttpClient(), mock.BaseUrl, Token), waits.Wait, maxPolls, PollInterval);
}

public class CreateFlowTests
{
    [Fact]
    public async Task CreatesMissingEndpointAndPollsToReady()
    {
        using var mock = new MockDatabricks((n, r) => n switch
        {
            0 => new(404, Fx.NotFound),
            1 => new(200, Fx.Deploying()),
            2 => new(200, Fx.Deploying(entA: "DEPLOYMENT_READY")),
            _ => new(200, Fx.Ready()),
        });
        var waits = new WaitRecorder();
        var result = await Fx.Deployer(mock, waits).DeployAsync(Fx.Spec(), CancellationToken.None);

        var reqs = mock.Snapshot();
        Assert.Equal(4, reqs.Length);
        Assert.Equal("GET", reqs[0].Method);
        Assert.Equal("/api/2.0/serving-endpoints/churn-scoring", reqs[0].RawUrl);
        Assert.Equal("Bearer " + Fx.Token, reqs[0].Headers["Authorization"]);
        Assert.Equal("application/json", reqs[0].Headers["Accept"]);

        Assert.Equal("POST", reqs[1].Method);
        Assert.Equal("/api/2.0/serving-endpoints", reqs[1].RawUrl);
        Assert.StartsWith("application/json", reqs[1].Headers["Content-Type"]);
        using var created = JsonDocument.Parse(reqs[1].Body);
        var root = created.RootElement;
        Assert.Equal("churn-scoring", root.GetProperty("name").GetString());
        var entities = root.GetProperty("config").GetProperty("served_entities");
        Assert.Equal(2, entities.GetArrayLength());
        Assert.Equal("churn-a", entities[0].GetProperty("name").GetString());
        Assert.Equal("ml.prod.churn_model", entities[0].GetProperty("entity_name").GetString());
        Assert.Equal("4", entities[0].GetProperty("entity_version").GetString());
        Assert.Equal("Small", entities[0].GetProperty("workload_size").GetString());
        Assert.True(entities[0].GetProperty("scale_to_zero_enabled").GetBoolean());
        Assert.Equal("5", entities[1].GetProperty("entity_version").GetString());
        var routes = root.GetProperty("config").GetProperty("traffic_config").GetProperty("routes");
        Assert.Equal("churn-a", routes[0].GetProperty("served_model_name").GetString());
        Assert.Equal(80, routes[0].GetProperty("traffic_percentage").GetInt32());
        Assert.Equal(20, routes[1].GetProperty("traffic_percentage").GetInt32());

        Assert.All(reqs.Skip(2), r =>
        {
            Assert.Equal("GET", r.Method);
            Assert.Equal("/api/2.0/serving-endpoints/churn-scoring", r.RawUrl);
        });
        Assert.Equal(new[] { Fx.PollInterval, Fx.PollInterval }, waits.Waits);

        Assert.True(result.Created, "a missing endpoint must be created, not updated");
        Assert.Equal("READY", result.Ready);
        Assert.Equal("NOT_UPDATING", result.ConfigUpdate);
        Assert.Equal("https://fixture.example/serving-endpoints/churn-scoring/invocations", result.EndpointUrl);
        Assert.Equal(2, result.Entities.Count);
        Assert.Equal(new EntityStatus("churn-a", "DEPLOYMENT_READY", ""), result.Entities[0]);
        Assert.Equal(new EntityStatus("churn-b", "DEPLOYMENT_READY", ""), result.Entities[1]);
        Assert.Equal(new RouteStatus("churn-a", 80), result.Routes[0]);
        Assert.Equal(new RouteStatus("churn-b", 20), result.Routes[1]);
        Assert.Empty(result.EntityErrors);
    }
}

public class UpdateFlowTests
{
    [Fact]
    public async Task UpdatesExistingEndpointViaPutConfig()
    {
        using var mock = new MockDatabricks((n, r) => n switch
        {
            0 => new(200, Fx.Ready()),
            1 => new(200, Fx.Deploying()),
            _ => new(200, Fx.Ready()),
        });
        var waits = new WaitRecorder();
        var result = await Fx.Deployer(mock, waits).DeployAsync(Fx.Spec(), CancellationToken.None);

        var reqs = mock.Snapshot();
        Assert.Equal(3, reqs.Length);
        Assert.Equal("PUT", reqs[1].Method);
        Assert.Equal("/api/2.0/serving-endpoints/churn-scoring/config", reqs[1].RawUrl);
        using var put = JsonDocument.Parse(reqs[1].Body);
        Assert.False(put.RootElement.TryGetProperty("name", out _),
            "the update body must not carry the endpoint name — it lives in the path");
        Assert.Equal(2, put.RootElement.GetProperty("served_entities").GetArrayLength());
        Assert.Equal(2, put.RootElement.GetProperty("traffic_config").GetProperty("routes").GetArrayLength());
        Assert.False(result.Created, "an existing endpoint must be updated, not re-created");
        Assert.Equal("READY", result.Ready);
        Assert.Single(waits.Waits);
    }

    [Fact]
    public async Task ConflictingUpdateIsTypedAndNeverRetried()
    {
        using var mock = new MockDatabricks((n, r) => n switch
        {
            0 => new(200, Fx.Ready()),
            _ => new(409, "{\"error_code\": \"RESOURCE_CONFLICT\", \"message\": \"Endpoint churn-scoring is currently being updated.\"}"),
        });
        var waits = new WaitRecorder();
        var ex = await Assert.ThrowsAsync<ConflictException>(
            () => Fx.Deployer(mock, waits).DeployAsync(Fx.Spec(), CancellationToken.None));

        Assert.Equal(409, ex.StatusCode);
        Assert.Equal("RESOURCE_CONFLICT", ex.ErrorCode);
        Assert.Contains("churn-scoring", ex.ApiMessage);
        Assert.Equal(2, mock.Snapshot().Length); // GET + exactly one PUT: conflicts must not be blindly retried
        Assert.Empty(waits.Waits);
        Assert.DoesNotContain(Fx.Token, ex.ToString());
    }
}

public class FailureAndBoundsTests
{
    [Fact]
    public async Task UpdateFailedStopsPollingAndPreservesPerEntityErrors()
    {
        const string buildError = "Container image build failed for served entity churn-b: exit code 137.";
        using var mock = new MockDatabricks((n, r) => n switch
        {
            0 => new(404, Fx.NotFound),
            1 => new(200, Fx.Deploying()),
            2 => new(200, Fx.Deploying(entA: "DEPLOYMENT_READY")),
            3 => new(200, Fx.Deploying(entA: "DEPLOYMENT_READY", entB: "DEPLOYMENT_FAILED",
                configUpdate: "UPDATE_FAILED", msgB: buildError)),
            _ => new(500, "{\"error_code\": \"MOCK_SCRIPT_ERROR\", \"message\": \"polled past a terminal state\"}"),
        });
        var waits = new WaitRecorder();
        var result = await Fx.Deployer(mock, waits).DeployAsync(Fx.Spec(), CancellationToken.None);

        Assert.Equal(4, mock.Snapshot().Length); // UPDATE_FAILED is terminal: no fifth request
        Assert.Equal(2, waits.Waits.Count);
        Assert.Equal("UPDATE_FAILED", result.ConfigUpdate);
        Assert.Equal("NOT_READY", result.Ready);
        Assert.Equal(2, result.Entities.Count);
        var failed = Assert.Single(result.EntityErrors);
        Assert.Equal("churn-b", failed.Key);
        Assert.Equal(buildError, failed.Value);
        Assert.DoesNotContain("churn-a", result.EntityErrors.Keys);
    }

    [Fact]
    public async Task PollingIsBoundedWithTypedTimeout()
    {
        using var mock = new MockDatabricks((n, r) => n switch
        {
            0 => new(404, Fx.NotFound),
            _ => new(200, Fx.Deploying()),
        });
        var waits = new WaitRecorder();
        var ex = await Assert.ThrowsAsync<PollTimeoutException>(
            () => Fx.Deployer(mock, waits, maxPolls: 3).DeployAsync(Fx.Spec(), CancellationToken.None));

        Assert.Equal(3, waits.Waits.Count);
        Assert.All(waits.Waits, w => Assert.Equal(Fx.PollInterval, w));
        Assert.Equal(5, mock.Snapshot().Length); // GET + POST + 3 bounded polls
        Assert.Equal("NOT_READY", ex.LastReady);
        Assert.Equal("IN_PROGRESS", ex.LastConfigUpdate);
    }

    [Fact]
    public async Task CallerCancellationAbortsPromptly()
    {
        using var mock = new MockDatabricks((n, r) => n switch
        {
            0 => new(404, Fx.NotFound),
            _ => new(200, Fx.Deploying()),
        });
        using var cts = new CancellationTokenSource();
        var waits = new WaitRecorder { OnWait = n => { if (n == 2) cts.Cancel(); } };
        await Assert.ThrowsAnyAsync<OperationCanceledException>(
            () => Fx.Deployer(mock, waits).DeployAsync(Fx.Spec(), cts.Token));

        Assert.Equal(2, waits.Waits.Count);
        Assert.Equal(3, mock.Snapshot().Length); // GET + POST + one poll; nothing after cancellation
    }

    [Fact]
    public async Task PermissionErrorIsTypedAndRedacted()
    {
        using var mock = new MockDatabricks((n, r) =>
            new(403, "{\"error_code\": \"PERMISSION_DENIED\", \"message\": \"User does not have EXECUTE on ml.prod.churn_model.\"}"));
        var ex = await Assert.ThrowsAsync<DbxApiException>(
            () => Fx.Deployer(mock, new WaitRecorder()).DeployAsync(Fx.Spec(), CancellationToken.None));

        Assert.Equal(403, ex.StatusCode);
        Assert.Equal("PERMISSION_DENIED", ex.ErrorCode);
        Assert.Equal("User does not have EXECUTE on ml.prod.churn_model.", ex.ApiMessage);
        Assert.Contains("PERMISSION_DENIED", ex.Message);
        Assert.DoesNotContain(Fx.Token, ex.ToString());
        Assert.DoesNotContain("Bearer", ex.ToString());
    }
}

public class ProtectedDocsTests
{
    [Fact]
    public void ResearchProvenanceAndContractArePinned()
    {
        using var sources = JsonDocument.Parse(File.ReadAllText(Path.Combine("docs", "official_sources.json")));
        var research = sources.RootElement.GetProperty("research");
        Assert.True(research.GetProperty("required").GetBoolean());
        var officialSources = research.GetProperty("official_sources");
        Assert.True(officialSources.GetArrayLength() >= 2, "at least two official sources required");
        foreach (var src in officialSources.EnumerateArray())
        {
            var url = src.GetProperty("url").GetString()!;
            Assert.StartsWith("https://", url);
            Assert.Contains("databricks", url);
            Assert.False(string.IsNullOrWhiteSpace(src.GetProperty("used_for").GetString()));
        }
        Assert.True(sources.RootElement.GetProperty("verified_facts").GetArrayLength() >= 4);

        using var contract = JsonDocument.Parse(File.ReadAllText(Path.Combine("docs", "contract.json")));
        var ops = contract.RootElement.GetProperty("operations");
        Assert.Equal("/api/2.0/serving-endpoints", ops.GetProperty("create").GetProperty("path").GetString());
        Assert.Equal("/api/2.0/serving-endpoints/{name}/config", ops.GetProperty("update_config").GetProperty("path").GetString());
        var states = contract.RootElement.GetProperty("states");
        Assert.Equal(4, states.GetProperty("config_update_enum").GetArrayLength());
        Assert.Equal(5, states.GetProperty("served_entity_deployment_enum").GetArrayLength());
    }
}
