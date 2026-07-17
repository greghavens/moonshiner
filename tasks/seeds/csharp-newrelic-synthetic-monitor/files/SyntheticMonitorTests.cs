// Acceptance tests for the synthetic-monitor reconciler (src/).
//
// Uses an injected HttpMessageHandler fake that records every request and
// replays scripted NerdGraph responses - the real HttpClient stack, no real
// New Relic, no real credentials, no Task.Delay: waiting goes through the
// injected delay function and is recorded. The wire contract the fake
// enforces is pinned in docs/contract.json. This file and everything under
// docs/ are protected; src/MonitorModels.cs is starter code you may extend.

using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using Acme.NewRelic.Synthetics;

public sealed class RecordedRequest
{
    public required string Method { get; init; }
    public required Uri Uri { get; init; }
    public required Dictionary<string, string> Headers { get; init; }
    public string? ContentType { get; init; }
    public string? Body { get; init; }

    public JsonNode Json() => JsonNode.Parse(Body ?? "{}")!;
    public string Document() => (string?)Json()["query"] ?? "";
    public JsonNode? Variables() => Json()["variables"];
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
        var (status, json) = Script.Count > 0 ? Script.Dequeue() : (200, "{\"data\":{}}");
        return new HttpResponseMessage((HttpStatusCode)status)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json"),
        };
    }
}

public class SyntheticMonitorTests
{
    static readonly JsonNode Contract = JsonNode.Parse(File.ReadAllText(
        Path.Combine(AppContext.BaseDirectory, "docs", "contract.json")))!;
    static readonly JsonNode Sources = JsonNode.Parse(File.ReadAllText(
        Path.Combine(AppContext.BaseDirectory, "docs", "official_sources.json")))!;

    static readonly string ApiKey = (string)Contract["transport"]!["fixture_api_key"]!;
    static readonly int AccountId = (int)Contract["fixtures"]!["account_id"]!;
    static readonly string MonitorName = (string)Contract["fixtures"]!["monitor_name"]!;
    static readonly string ExistingGuid = (string)Contract["fixtures"]!["existing_guid"]!;
    static readonly string CreatedGuid = (string)Contract["fixtures"]!["created_guid"]!;
    static readonly string Script = (string)Contract["fixtures"]!["script"]!;
    static readonly string SecureRef = "$secure." + (string)Contract["fixtures"]!["secure_key"]!;

    static JsonNode Fx(string name) =>
        JsonNode.Parse(Contract["fixtures"]![name]!.ToJsonString())!;

    sealed class Harness
    {
        public ScriptedHandler Handler { get; } = new();
        public List<TimeSpan> Delays { get; } = new();

        public NerdGraphClient Client() => new(
            "https://api.newrelic.com/graphql",
            ApiKey,
            Handler,
            delay =>
            {
                Delays.Add(delay);
                return Task.CompletedTask;
            });

        public MonitorReconciler Reconciler() => new(Client(), AccountId);

        public void QueueSearch(params JsonNode[] entities)
        {
            var results = new JsonObject
            {
                ["entities"] = new JsonArray(entities),
                ["nextCursor"] = null,
            };
            Handler.Queue(200, new JsonObject
            {
                ["data"] = new JsonObject
                {
                    ["actor"] = new JsonObject
                    {
                        ["entitySearch"] = new JsonObject { ["results"] = results },
                    },
                },
            });
        }

        public void QueueMutation(string field, string? guid, JsonNode? errors)
        {
            var payload = new JsonObject { ["errors"] = errors };
            payload["monitor"] = guid is null
                ? null
                : new JsonObject { ["guid"] = guid, ["name"] = MonitorName };
            Handler.Queue(200, new JsonObject
            {
                ["data"] = new JsonObject { [field] = payload },
            });
        }

        public void QueueTagReplace(JsonNode? errors)
        {
            Handler.Queue(200, new JsonObject
            {
                ["data"] = new JsonObject
                {
                    ["taggingReplaceTagsOnEntity"] = new JsonObject { ["errors"] = errors },
                },
            });
        }
    }

    static JsonNode ExistingEntity(string tagsFixture = "current_tags") => new JsonObject
    {
        ["guid"] = ExistingGuid,
        ["name"] = MonitorName,
        ["accountId"] = AccountId,
        ["monitorType"] = "SCRIPT_API",
        ["tags"] = Fx(tagsFixture),
    };

    static Dictionary<string, IReadOnlyList<string>> DesiredTags() => new()
    {
        ["team"] = new[] { "payments" },
        ["tier"] = new[] { "1" },
    };

    static DesiredScriptApiMonitor Desired(
        string? script, string? period = null, string? guid = null) => new(
        Name: MonitorName,
        Period: period ?? (string)Contract["fixtures"]!["period"]!,
        Status: (string)Contract["fixtures"]!["status"]!,
        PublicLocations: new[] { "US_EAST_1", "EU_WEST_1" },
        Script: script,
        ApdexTarget: 7.0,
        Tags: DesiredTags())
    {
        Guid = guid,
    };

    static JsonObject ExpectedMonitorInput(bool withScript, string? period = null)
    {
        var monitor = new JsonObject
        {
            ["name"] = MonitorName,
            ["period"] = period ?? (string)Contract["fixtures"]!["period"]!,
            ["status"] = "ENABLED",
            ["locations"] = new JsonObject
            {
                ["public"] = new JsonArray("US_EAST_1", "EU_WEST_1"),
            },
            ["runtime"] = new JsonObject
            {
                ["runtimeType"] = "NODE_API",
                ["runtimeTypeVersion"] = "22.20.0",
                ["scriptLanguage"] = "JAVASCRIPT",
            },
            ["apdexTarget"] = 7.0,
        };
        if (withScript)
        {
            monitor["script"] = Script;
        }
        return monitor;
    }

    static void AssertDeepEquals(JsonNode? expected, JsonNode? actual, string what)
    {
        Assert.True(JsonNode.DeepEquals(expected, actual),
            $"{what}\nexpected: {expected?.ToJsonString()}\nactual:   {actual?.ToJsonString()}");
    }

    [Fact]
    public async Task TransportFollowsTheDocumentedNerdGraphContract()
    {
        var h = new Harness();
        h.QueueSearch(ExistingEntity());
        h.QueueMutation("syntheticsUpdateScriptApiMonitor", ExistingGuid, null);
        h.QueueTagReplace(null);
        await h.Reconciler().ReconcileAsync(Desired(script: null));

        var req = h.Handler.Requests[0];
        Assert.Equal("POST", req.Method);
        Assert.Equal("https://api.newrelic.com/graphql", req.Uri.ToString());
        Assert.Equal("application/json", req.ContentType);
        Assert.Equal(ApiKey, req.Headers["API-Key"]);
        Assert.False(req.Headers.ContainsKey("Authorization"),
            "NerdGraph auth is the API-Key header, not a Bearer token");
        var keys = ((JsonObject)req.Json()).Select(p => p.Key).OrderBy(k => k, StringComparer.Ordinal);
        Assert.Equal(new[] { "query", "variables" }, keys);
    }

    [Fact]
    public async Task DiscoveryUsesTheDocumentedSynthEntitySearch()
    {
        var h = new Harness();
        h.QueueSearch();
        h.QueueMutation("syntheticsCreateScriptApiMonitor", CreatedGuid, null);
        h.QueueTagReplace(null);
        await h.Reconciler().ReconcileAsync(Desired(script: Script));

        var search = h.Handler.Requests[0];
        var doc = search.Document();
        Assert.Contains("entitySearch", doc);
        Assert.Contains("SyntheticMonitorEntityOutline", doc);
        Assert.Contains("monitorType", doc);
        Assert.Contains("nextCursor", doc);
        var expectedQuery = (string)Contract["discovery"]!["base_query"]!
            + " AND name = '" + MonitorName + "'";
        Assert.Equal(expectedQuery, (string?)search.Variables()?["query"]);
        Assert.DoesNotContain(MonitorName, doc);
    }

    [Fact]
    public async Task MissingMonitorIsCreatedWithTheDocumentedInput()
    {
        var h = new Harness();
        h.QueueSearch();
        h.QueueMutation("syntheticsCreateScriptApiMonitor", CreatedGuid, null);
        h.QueueTagReplace(null);
        var outcome = await h.Reconciler().ReconcileAsync(Desired(script: Script));

        Assert.Equal(3, h.Handler.Requests.Count);
        var create = h.Handler.Requests[1];
        var doc = create.Document();
        Assert.Contains("syntheticsCreateScriptApiMonitor(", doc);
        Assert.Contains("errors", doc);
        Assert.Contains("description", doc);
        Assert.Contains("type", doc);
        Assert.Contains("guid", doc);
        Assert.DoesNotContain("syntheticsCreateScriptBrowserMonitor", doc);

        var vars = create.Variables()!;
        Assert.Equal(AccountId, (int)vars["accountId"]!);
        AssertDeepEquals(ExpectedMonitorInput(withScript: true), vars["monitor"],
            "the create input must match the documented monitor shape exactly");
        Assert.Contains(SecureRef, (string)vars["monitor"]!["script"]!);

        Assert.Equal("created", outcome.Action);
        Assert.Equal(CreatedGuid, outcome.Guid);
        Assert.Empty(outcome.TagErrors);

        var tag = h.Handler.Requests[2];
        Assert.Contains("taggingReplaceTagsOnEntity(", tag.Document());
        Assert.Equal(CreatedGuid, (string?)tag.Variables()?["guid"]);
        AssertDeepEquals(Fx("matching_tags"), tag.Variables()?["tags"],
            "a fresh create tags every desired key");
        Assert.Equal(new[] { "team", "tier" }, outcome.TagKeysReplaced);
    }

    [Fact]
    public async Task ExistingMonitorIsUpdatedByGuidWithoutClobberingTheScript()
    {
        var h = new Harness();
        h.QueueSearch(ExistingEntity());
        h.QueueMutation("syntheticsUpdateScriptApiMonitor", ExistingGuid, null);
        h.QueueTagReplace(null);
        var outcome = await h.Reconciler().ReconcileAsync(
            Desired(script: null, period: (string)Contract["fixtures"]!["changed_period"]!));

        var update = h.Handler.Requests[1];
        Assert.Contains("syntheticsUpdateScriptApiMonitor(", update.Document());
        var vars = (JsonObject)update.Variables()!;
        Assert.Equal(ExistingGuid, (string?)vars["guid"]);
        Assert.False(vars.ContainsKey("accountId"),
            "updates address the monitor by guid, not account");
        var monitor = (JsonObject)vars["monitor"]!;
        Assert.False(monitor.ContainsKey("script"),
            "no desired script means the script field is omitted so stored $secure references survive");
        AssertDeepEquals(
            ExpectedMonitorInput(withScript: false,
                period: (string)Contract["fixtures"]!["changed_period"]!),
            monitor,
            "the update input must match the documented monitor shape");

        Assert.Equal("updated", outcome.Action);
        Assert.Equal(ExistingGuid, outcome.Guid);
    }

    [Fact]
    public async Task TagReconciliationTouchesOnlyManagedKeysThatDiffer()
    {
        var h = new Harness();
        h.QueueSearch(ExistingEntity("current_tags"));
        h.QueueMutation("syntheticsUpdateScriptApiMonitor", ExistingGuid, null);
        h.QueueTagReplace(null);
        var outcome = await h.Reconciler().ReconcileAsync(Desired(script: null));

        var tag = h.Handler.Requests[2];
        AssertDeepEquals(Fx("matching_tags"), tag.Variables()?["tags"],
            "team differs and tier is missing, so exactly those keys are replaced");
        Assert.DoesNotContain("owner", tag.Body ?? "", StringComparison.Ordinal);
        Assert.Equal(new[] { "team", "tier" }, outcome.TagKeysReplaced);
    }

    [Fact]
    public async Task MatchingTagsSendNoTaggingMutation()
    {
        var h = new Harness();
        h.QueueSearch(ExistingEntity("matching_tags"));
        h.QueueMutation("syntheticsUpdateScriptApiMonitor", ExistingGuid, null);
        var outcome = await h.Reconciler().ReconcileAsync(Desired(script: null));

        Assert.Equal(2, h.Handler.Requests.Count);
        Assert.Empty(outcome.TagKeysReplaced);
        Assert.Empty(outcome.TagErrors);
    }

    [Fact]
    public async Task DirectGuidSkipsDiscoveryAndReplacesAllDesiredKeys()
    {
        var h = new Harness();
        h.QueueMutation("syntheticsUpdateScriptApiMonitor", ExistingGuid, null);
        h.QueueTagReplace(null);
        var outcome = await h.Reconciler().ReconcileAsync(
            Desired(script: null, guid: ExistingGuid));

        Assert.Equal(2, h.Handler.Requests.Count);
        Assert.DoesNotContain("entitySearch", h.Handler.Requests[0].Document());
        Assert.Contains("syntheticsUpdateScriptApiMonitor(", h.Handler.Requests[0].Document());
        AssertDeepEquals(Fx("matching_tags"), h.Handler.Requests[1].Variables()?["tags"],
            "without discovery every desired key is replaced");
        Assert.Equal("updated", outcome.Action);
    }

    [Fact]
    public async Task AmbiguousDiscoveryFails()
    {
        var h = new Harness();
        var second = (JsonObject)ExistingEntity().DeepClone();
        second["guid"] = (string)Contract["fixtures"]!["second_guid"]!;
        h.QueueSearch(ExistingEntity(), second);
        await Assert.ThrowsAsync<AmbiguousMonitorException>(
            () => h.Reconciler().ReconcileAsync(Desired(script: null)));
        Assert.Single(h.Handler.Requests);
    }

    [Fact]
    public async Task PerFieldMutationErrorsSurfaceAndStopTheFlow()
    {
        var h = new Harness();
        h.QueueSearch(ExistingEntity());
        h.QueueMutation("syntheticsUpdateScriptApiMonitor", null,
            new JsonArray(Fx("field_error")));
        var ex = await Assert.ThrowsAsync<SyntheticsMutationException>(
            () => h.Reconciler().ReconcileAsync(Desired(script: null)));

        var error = Assert.Single(ex.Errors);
        Assert.Equal((string)Contract["fixtures"]!["field_error"]!["description"]!,
            error.Description);
        Assert.Equal("BAD_REQUEST", error.Type);
        Assert.Contains(error.Description, ex.Message);
        Assert.DoesNotContain(ApiKey, ex.Message);
        Assert.Equal(2, h.Handler.Requests.Count);
    }

    [Fact]
    public async Task TagFailureAfterCreateIsAPartialSuccess()
    {
        var h = new Harness();
        h.QueueSearch();
        h.QueueMutation("syntheticsCreateScriptApiMonitor", CreatedGuid, null);
        h.QueueTagReplace(new JsonArray(new JsonObject
        {
            ["message"] = (string)Contract["fixtures"]!["tag_error_message"]!,
        }));
        var outcome = await h.Reconciler().ReconcileAsync(Desired(script: Script));

        Assert.Equal("created", outcome.Action);
        Assert.Equal(CreatedGuid, outcome.Guid);
        var message = Assert.Single(outcome.TagErrors);
        Assert.Equal((string)Contract["fixtures"]!["tag_error_message"]!, message);
    }

    [Fact]
    public async Task ThrottledRequestsRetryWithDoublingDelays()
    {
        var h = new Harness();
        h.Handler.Queue(429, (string)Contract["fixtures"]!["throttle_body"]!);
        h.Handler.Queue(429, (string)Contract["fixtures"]!["throttle_body"]!);
        h.QueueSearch(ExistingEntity("matching_tags"));
        h.QueueMutation("syntheticsUpdateScriptApiMonitor", ExistingGuid, null);
        var outcome = await h.Reconciler().ReconcileAsync(Desired(script: null));

        Assert.Equal(4, h.Handler.Requests.Count);
        Assert.Equal(new[] { TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(2) }, h.Delays);
        Assert.Equal(h.Handler.Requests[0].Body, h.Handler.Requests[1].Body);
        Assert.Equal(h.Handler.Requests[0].Body, h.Handler.Requests[2].Body);
        Assert.Equal("updated", outcome.Action);
    }

    [Fact]
    public async Task RetryExhaustionSurfacesTheStatus()
    {
        var h = new Harness();
        for (var i = 0; i < 3; i++)
        {
            h.Handler.Queue(429, (string)Contract["fixtures"]!["throttle_body"]!);
        }
        var ex = await Assert.ThrowsAsync<NerdGraphHttpException>(
            () => h.Reconciler().ReconcileAsync(Desired(script: null)));
        Assert.Equal(429, ex.Status);
        Assert.Equal(3, h.Handler.Requests.Count);
        Assert.Equal(2, h.Delays.Count);
        Assert.DoesNotContain(ApiKey, ex.Message);
    }

    [Fact]
    public async Task InvalidDesiredStateIsRejectedBeforeTheWire()
    {
        var h = new Harness();
        await Assert.ThrowsAsync<ArgumentException>(
            () => h.Reconciler().ReconcileAsync(Desired(script: null, period: "EVERY_2_MINUTES")));
        await Assert.ThrowsAsync<ArgumentException>(
            () => h.Reconciler().ReconcileAsync(Desired(script: null) with { Status = "PAUSED" }));
        await Assert.ThrowsAsync<ArgumentException>(
            () => h.Reconciler().ReconcileAsync(Desired(script: null) with { Name = "bad'name" }));
        Assert.Empty(h.Handler.Requests);
    }

    [Fact]
    public async Task TheKeyAndNoSecretValuesEverTravelInBodies()
    {
        var h = new Harness();
        h.QueueSearch();
        h.QueueMutation("syntheticsCreateScriptApiMonitor", CreatedGuid, null);
        h.QueueTagReplace(null);
        await h.Reconciler().ReconcileAsync(Desired(script: Script));

        foreach (var req in h.Handler.Requests)
        {
            Assert.DoesNotContain(ApiKey, req.Uri.ToString());
            Assert.DoesNotContain(ApiKey, req.Body ?? "");
        }
        Assert.Contains(SecureRef, h.Handler.Requests[1].Body ?? "");
    }

    [Fact]
    public void ProtectedProvenanceFixturesAreIntact()
    {
        Assert.True((bool)Sources["research"]!["required"]!);
        Assert.True(((JsonArray)Sources["research"]!["official_sources"]!).Count >= 2);
        Assert.True(((JsonArray)Sources["verified_facts"]!).Count >= 4);
        Assert.Equal("syntheticsCreateScriptApiMonitor", (string)Contract["mutations"]!["create"]!);
        Assert.Equal("syntheticsUpdateScriptApiMonitor", (string)Contract["mutations"]!["update"]!);
        Assert.Equal("NODE_API",
            (string)Contract["mutations"]!["monitor_input"]!["runtime"]!["runtimeType"]!);
        Assert.Equal("22.20.0",
            (string)Contract["mutations"]!["monitor_input"]!["runtime"]!["runtimeTypeVersion"]!);
        Assert.Equal("JAVASCRIPT",
            (string)Contract["mutations"]!["monitor_input"]!["runtime"]!["scriptLanguage"]!);
        Assert.Contains("EVERY_15_MINUTES",
            ((JsonArray)Contract["mutations"]!["monitor_input"]!["period_enum"]!)
                .Select(n => (string)n!));
        Assert.Equal("taggingReplaceTagsOnEntity", (string)Contract["tagging"]!["replace_mutation"]!);
        Assert.Equal("API-Key", (string)Contract["transport"]!["auth_header"]!);
        Assert.Equal("domain = 'SYNTH' AND type = 'MONITOR'",
            (string)Contract["discovery"]!["base_query"]!);
    }
}
