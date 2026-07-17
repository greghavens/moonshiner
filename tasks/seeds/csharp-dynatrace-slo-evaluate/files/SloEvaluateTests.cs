// Acceptance tests for the Dynatrace SLO client (src/).
//
// Uses an injected HttpMessageHandler fake that records every request and
// replays scripted platform responses — the real HttpClient stack, no real
// Dynatrace, no real credentials, no Task.Delay: waiting is injected and
// recorded. The wire contract the fake enforces is pinned in
// docs/contract.json. This file and everything under docs/ are protected;
// src/SloModels.cs is starter code you may extend.

using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Acme.Dynatrace.Slo;

public sealed class RecordedRequest
{
    public required string Method { get; init; }
    public required Uri Uri { get; init; }
    public required Dictionary<string, string> Headers { get; init; }
    public string? ContentType { get; init; }
    public string? Body { get; init; }

    public Dictionary<string, string> Query()
    {
        var result = new Dictionary<string, string>();
        var raw = Uri.Query.TrimStart('?');
        if (raw.Length == 0)
        {
            return result;
        }
        foreach (var pair in raw.Split('&'))
        {
            var eq = pair.IndexOf('=');
            var key = eq < 0 ? pair : pair[..eq];
            var value = eq < 0 ? "" : pair[(eq + 1)..];
            result[Uri.UnescapeDataString(key)] = Uri.UnescapeDataString(value);
        }
        return result;
    }
}

public sealed class ScriptedHandler : HttpMessageHandler
{
    public List<RecordedRequest> Requests { get; } = new();
    public Queue<(int Status, string Json)> Script { get; } = new();

    public void Queue(int status, string json) => Script.Enqueue((status, json));

    public void Queue(int status, JsonNode node) => Script.Enqueue((status, node.ToJsonString()));

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var header in request.Headers)
        {
            headers[header.Key] = string.Join(",", header.Value);
        }
        string? body = null;
        string? contentType = null;
        if (request.Content is not null)
        {
            body = await request.Content.ReadAsStringAsync(cancellationToken);
            contentType = request.Content.Headers.ContentType?.MediaType;
        }
        Requests.Add(new RecordedRequest
        {
            Method = request.Method.Method,
            Uri = request.RequestUri!,
            Headers = headers,
            ContentType = contentType,
            Body = body,
        });
        var (status, json) = Script.Count > 0 ? Script.Dequeue() : (200, "{}");
        return new HttpResponseMessage((HttpStatusCode)status)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json"),
        };
    }
}

public class SloEvaluateTests
{
    static readonly JsonNode Contract = JsonNode.Parse(File.ReadAllText(
        Path.Combine(AppContext.BaseDirectory, "docs", "contract.json")))!;
    static readonly JsonNode Sources = JsonNode.Parse(File.ReadAllText(
        Path.Combine(AppContext.BaseDirectory, "docs", "official_sources.json")))!;

    static readonly string Token = (string)Contract["auth"]!["fixture_token"]!;
    static readonly string EnvId = (string)Contract["fixtures"]!["environment_id"]!;
    static readonly string SloId = (string)Contract["fixtures"]!["slo_id"]!;
    const string BasePath = "/platform/slo/v1/slos";

    static JsonNode Fixture(string name) =>
        JsonNode.Parse(Contract["fixtures"]![name]!.ToJsonString())!;

    static JsonNode ListPage(JsonNode[] slos, string? nextPageKey = null)
    {
        var page = new JsonObject
        {
            ["slos"] = new JsonArray(slos),
            ["totalCount"] = slos.Length,
        };
        if (nextPageKey is not null)
        {
            page["nextPageKey"] = nextPageKey;
        }
        return page;
    }

    sealed class Harness
    {
        public ScriptedHandler Handler { get; } = new();
        public List<TimeSpan> Delays { get; } = new();

        public SloClient Client(string? environmentUrl = null) => new(
            environmentUrl ?? $"https://{EnvId}.apps.dynatrace.com",
            Token,
            Handler,
            delay =>
            {
                Delays.Add(delay);
                return Task.CompletedTask;
            });
    }

    static SloDefinition CheckoutDefinition(string? description =
        "Checkout service availability") => new(
        Name: "checkout-availability",
        Description: description,
        CustomSliIndicator: (string)Contract["fixtures"]!["existing_slo"]!["customSli"]!["indicator"]!,
        Criteria: new[] { new SloCriteria("now-7d", "now", 99.5, 99.8) },
        Tags: description is null ? Array.Empty<string>() : new[] { "team:checkout" },
        ExternalId: null);

    [Fact]
    public void LatestAndClassicUrlFamiliesAreDistinguished()
    {
        Assert.Equal($"https://{EnvId}.apps.dynatrace.com/platform/slo/v1/slos",
            SloApiUrls.Latest(EnvId));
        Assert.Equal($"https://{EnvId}.live.dynatrace.com/api/v2/slo",
            SloApiUrls.Classic(EnvId));
    }

    [Fact]
    public void ClassicConfigurationsAreRejectedUpFront()
    {
        var h = new Harness();
        var byHost = Assert.ThrowsAny<ArgumentException>(
            () => h.Client($"https://{EnvId}.live.dynatrace.com"));
        Assert.Contains("platform/slo/v1/slos", byHost.Message);
        var byPath = Assert.ThrowsAny<ArgumentException>(
            () => h.Client($"https://{EnvId}.apps.dynatrace.com/api/v2/slo"));
        Assert.Contains("platform/slo/v1/slos", byPath.Message);
        Assert.Empty(h.Handler.Requests);
    }

    [Fact]
    public async Task CreatePostsTheDocumentedBodyWhenTheSloIsMissing()
    {
        var h = new Harness();
        h.Handler.Queue(200, ListPage(new[] { Fixture("unrelated_slo") }));
        var created = Fixture("existing_slo");
        created["version"] = "1";
        h.Handler.Queue(201, created);

        var record = await h.Client().CreateOrUpdateAsync(CheckoutDefinition());

        Assert.Equal(2, h.Handler.Requests.Count);
        var list = h.Handler.Requests[0];
        Assert.Equal("GET", list.Method);
        Assert.Equal(BasePath, list.Uri.AbsolutePath);
        Assert.Equal(new Dictionary<string, string> { ["page-size"] = "200" }, list.Query());

        var create = h.Handler.Requests[1];
        Assert.Equal("POST", create.Method);
        Assert.Equal(BasePath, create.Uri.AbsolutePath);
        Assert.Equal("application/json", create.ContentType);
        using var body = JsonDocument.Parse(create.Body!);
        var root = body.RootElement;
        Assert.Equal("checkout-availability", root.GetProperty("name").GetString());
        Assert.Equal("Checkout service availability",
            root.GetProperty("description").GetString());
        Assert.Equal((string)Contract["fixtures"]!["existing_slo"]!["customSli"]!["indicator"]!,
            root.GetProperty("customSli").GetProperty("indicator").GetString());
        var criteria = root.GetProperty("criteria")[0];
        Assert.Equal("now-7d", criteria.GetProperty("timeframeFrom").GetString());
        Assert.Equal("now", criteria.GetProperty("timeframeTo").GetString());
        Assert.Equal(99.5, criteria.GetProperty("target").GetDouble());
        Assert.Equal(99.8, criteria.GetProperty("warning").GetDouble());
        Assert.Equal("team:checkout", root.GetProperty("tags")[0].GetString());
        Assert.False(root.TryGetProperty("externalId", out _),
            "null optionals must be omitted, not sent as null");

        foreach (var req in h.Handler.Requests)
        {
            Assert.Equal($"Bearer {Token}", req.Headers["Authorization"]);
        }
        Assert.Equal(SloId, record.Id);
        Assert.Equal("1", record.Version);
    }

    [Fact]
    public async Task NullOptionalFieldsAreOmittedFromTheCreateBody()
    {
        var h = new Harness();
        h.Handler.Queue(200, ListPage(Array.Empty<JsonNode>()));
        h.Handler.Queue(201, Fixture("existing_slo"));

        var definition = CheckoutDefinition(description: null) with
        {
            Criteria = new[] { new SloCriteria("now-7d", null, 99.5, null) },
        };
        await h.Client().CreateOrUpdateAsync(definition);

        using var body = JsonDocument.Parse(h.Handler.Requests[1].Body!);
        var root = body.RootElement;
        Assert.False(root.TryGetProperty("description", out _));
        Assert.False(root.TryGetProperty("tags", out _));
        Assert.False(root.TryGetProperty("externalId", out _));
        var criteria = root.GetProperty("criteria")[0];
        Assert.False(criteria.TryGetProperty("timeframeTo", out _));
        Assert.False(criteria.TryGetProperty("warning", out _));
    }

    [Fact]
    public async Task UpdateUsesTheVersionFromTheImmediatelyPrecedingGet()
    {
        var h = new Harness();
        h.Handler.Queue(200, ListPage(new[] { Fixture("existing_slo") }));
        h.Handler.Queue(200, Fixture("existing_slo"));
        var updated = Fixture("existing_slo");
        updated["version"] = "6";
        h.Handler.Queue(200, updated);

        var record = await h.Client().CreateOrUpdateAsync(CheckoutDefinition());

        Assert.Equal(3, h.Handler.Requests.Count);
        var get = h.Handler.Requests[1];
        Assert.Equal("GET", get.Method);
        Assert.Equal($"{BasePath}/{SloId}", get.Uri.AbsolutePath);
        var put = h.Handler.Requests[2];
        Assert.Equal("PUT", put.Method);
        Assert.Equal($"{BasePath}/{SloId}", put.Uri.AbsolutePath);
        Assert.Equal(new Dictionary<string, string> { ["optimistic-locking-version"] = "5" },
            put.Query());
        Assert.Equal("6", record.Version);
    }

    [Fact]
    public async Task VersionConflictRefetchesAndRetriesExactlyOnce()
    {
        var h = new Harness();
        h.Handler.Queue(200, ListPage(new[] { Fixture("existing_slo") }));
        h.Handler.Queue(200, Fixture("existing_slo"));
        h.Handler.Queue(409, Fixture("error_404")); // any error envelope
        var fresh = Fixture("existing_slo");
        fresh["version"] = "6";
        h.Handler.Queue(200, fresh);
        var updated = Fixture("existing_slo");
        updated["version"] = "7";
        h.Handler.Queue(200, updated);

        var record = await h.Client().CreateOrUpdateAsync(CheckoutDefinition());

        Assert.Equal(5, h.Handler.Requests.Count);
        Assert.Equal("5", h.Handler.Requests[2].Query()["optimistic-locking-version"]);
        Assert.Equal("GET", h.Handler.Requests[3].Method);
        Assert.Equal("6", h.Handler.Requests[4].Query()["optimistic-locking-version"]);
        Assert.Equal("7", record.Version);
    }

    [Fact]
    public async Task ASecondConflictSurfacesInsteadOfLooping()
    {
        var h = new Harness();
        h.Handler.Queue(200, ListPage(new[] { Fixture("existing_slo") }));
        h.Handler.Queue(200, Fixture("existing_slo"));
        h.Handler.Queue(409, Fixture("error_404"));
        var fresh = Fixture("existing_slo");
        fresh["version"] = "6";
        h.Handler.Queue(200, fresh);
        h.Handler.Queue(409, Fixture("error_404"));

        await Assert.ThrowsAsync<SloConflictException>(
            () => h.Client().CreateOrUpdateAsync(CheckoutDefinition()));
        Assert.Equal(5, h.Handler.Requests.Count);
    }

    [Fact]
    public async Task ListWalksPageKeyCursorsWithNoOtherParameters()
    {
        var h = new Harness();
        h.Handler.Queue(200, ListPage(new[] { Fixture("unrelated_slo") }, "pk+1"));
        h.Handler.Queue(200, ListPage(new[] { Fixture("existing_slo") }));
        h.Handler.Queue(200, Fixture("existing_slo"));
        h.Handler.Queue(200, Fixture("existing_slo"));

        await h.Client().CreateOrUpdateAsync(CheckoutDefinition());

        var second = h.Handler.Requests[1];
        Assert.Equal(BasePath, second.Uri.AbsolutePath);
        Assert.Equal(new Dictionary<string, string> { ["page-key"] = "pk+1" }, second.Query());
    }

    [Fact]
    public async Task ImmediateEvaluationResultsNeedNoPolling()
    {
        var h = new Harness();
        h.Handler.Queue(200, new JsonObject
        {
            ["evaluationResults"] = Fixture("warning_results"),
        });

        var report = await h.Client().EvaluateAsync(
            SloId, TimeSpan.FromSeconds(2), maxPolls: 5);

        Assert.Single(h.Handler.Requests);
        var start = h.Handler.Requests[0];
        Assert.Equal("POST", start.Method);
        Assert.Equal($"{BasePath}/evaluation:start", start.Uri.AbsolutePath);
        Assert.Equal("application/json", start.ContentType);
        using var body = JsonDocument.Parse(start.Body!);
        Assert.Equal(SloId, body.RootElement.GetProperty("id").GetString());
        Assert.Empty(h.Delays);

        Assert.Equal(SloId, report.SloId);
        Assert.Equal(new[] { "now-30d -> now", "now-7d -> now" },
            report.Entries.Select(e => e.Criteria).ToArray());
        Assert.Equal(SloStatus.Success, report.Entries[0].Status);
        Assert.Equal(SloStatus.Warning, report.Entries[1].Status);
        Assert.Equal(SloStatus.Warning, report.Overall);
    }

    [Fact]
    public async Task AsyncEvaluationPollsWithTheTokenUntilNothingIsPending()
    {
        var h = new Harness();
        var token = (string)Contract["fixtures"]!["evaluation_token"]!;
        h.Handler.Queue(200, new JsonObject
        {
            ["evaluationToken"] = token,
            ["ttlSeconds"] = 600,
            ["evaluationResults"] = Fixture("pending_result"),
        });
        h.Handler.Queue(200, new JsonObject
        {
            ["evaluationToken"] = token,
            ["evaluationResults"] = Fixture("pending_result"),
        });
        h.Handler.Queue(200, new JsonObject
        {
            ["evaluationResults"] = Fixture("final_results"),
        });

        var interval = TimeSpan.FromSeconds(3);
        var report = await h.Client().EvaluateAsync(SloId, interval, maxPolls: 5);

        Assert.Equal(3, h.Handler.Requests.Count);
        foreach (var poll in h.Handler.Requests.Skip(1))
        {
            Assert.Equal("GET", poll.Method);
            Assert.Equal($"{BasePath}/evaluation:poll", poll.Uri.AbsolutePath);
            Assert.Equal(new Dictionary<string, string> { ["evaluation-token"] = token },
                poll.Query());
        }
        Assert.Equal(new[] { interval, interval }, h.Delays.ToArray());

        Assert.Equal(SloStatus.Failure, report.Overall);
        var week = report.Entries.Single(e => e.Criteria == "now-7d -> now");
        Assert.Equal(SloStatus.Success, week.Status);
        Assert.Equal(99.92, week.Value);
        Assert.Equal(0.42, week.ErrorBudget);
        var month = report.Entries.Single(e => e.Criteria == "now-30d -> now");
        Assert.Equal(SloStatus.Failure, month.Status);
        Assert.Equal(-0.3, month.ErrorBudget);
        Assert.Equal("error budget exhausted", month.Message);
    }

    [Fact]
    public async Task EvaluationTimesOutWithTheTokenAfterMaxPolls()
    {
        var h = new Harness();
        var token = (string)Contract["fixtures"]!["evaluation_token"]!;
        h.Handler.Queue(200, new JsonObject
        {
            ["evaluationToken"] = token,
            ["evaluationResults"] = Fixture("pending_result"),
        });
        for (var i = 0; i < 3; i++)
        {
            h.Handler.Queue(200, new JsonObject
            {
                ["evaluationToken"] = token,
                ["evaluationResults"] = Fixture("pending_result"),
            });
        }

        var error = await Assert.ThrowsAsync<SloEvaluationTimeoutException>(
            () => h.Client().EvaluateAsync(SloId, TimeSpan.FromSeconds(1), maxPolls: 3));

        Assert.Equal(token, error.EvaluationToken);
        Assert.Equal(4, h.Handler.Requests.Count);
        Assert.Equal(3, h.Delays.Count);
    }

    [Fact]
    public async Task PlatformErrorsKeepStatusAndMessageButNeverTheToken()
    {
        var h = new Harness();
        h.Handler.Queue(404, Fixture("error_404"));
        var error = await Assert.ThrowsAsync<SloApiException>(
            () => h.Client().EvaluateAsync(SloId, TimeSpan.FromSeconds(1), maxPolls: 1));
        Assert.Equal(404, error.Status);
        Assert.Contains("SLO not found", error.Message);
        Assert.DoesNotContain(Token, error.Message);
    }

    [Fact]
    public async Task TheBearerTokenTravelsOnlyInTheAuthorizationHeader()
    {
        var h = new Harness();
        h.Handler.Queue(200, new JsonObject
        {
            ["evaluationResults"] = Fixture("final_results"),
        });
        await h.Client().EvaluateAsync(SloId, TimeSpan.FromSeconds(1), maxPolls: 1);
        foreach (var req in h.Handler.Requests)
        {
            Assert.Equal($"Bearer {Token}", req.Headers["Authorization"]);
            Assert.DoesNotContain(Token, req.Uri.ToString());
            Assert.DoesNotContain(Token, req.Body ?? "");
        }
    }

    [Fact]
    public void ProtectedProvenanceFixturesAreIntact()
    {
        Assert.True((bool)Sources["research"]!["required"]!);
        Assert.True(Sources["research"]!["official_sources"]!.AsArray().Count >= 2);
        Assert.Equal("/platform/slo/v1/slos",
            (string)Contract["url_families"]!["latest"]!["base_path"]!);
        Assert.Equal("/api/v2/slo",
            (string)Contract["url_families"]!["classic"]!["base_path"]!);
        var statuses = Contract["status_enum"]!.AsArray()
            .Select(n => (string)n!).ToArray();
        Assert.Contains("PENDING", statuses);
        Assert.Contains("FAILURE", statuses);
    }
}
