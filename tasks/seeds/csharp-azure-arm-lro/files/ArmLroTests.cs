// Protected acceptance tests for the ArmLro client.
// Hermetic: every HTTP exchange goes through ScriptedHandler; nothing leaves the process.
using System.Net;
using System.Text;
using System.Text.Json;
using ArmLro;

namespace ArmLroTests;

public sealed record RecordedRequest(
    HttpMethod Method,
    Uri Uri,
    string? Authorization,
    string? ContentType,
    string Body);

public sealed class ScriptedHandler : HttpMessageHandler
{
    private readonly List<(Func<HttpRequestMessage, bool> Match, Queue<Func<HttpResponseMessage>> Responses)> _routes = new();
    public List<RecordedRequest> Requests { get; } = new();

    public void Enqueue(Func<HttpRequestMessage, bool> match, params Func<HttpResponseMessage>[] responses)
    {
        _routes.Add((match, new Queue<Func<HttpResponseMessage>>(responses)));
    }

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
    {
        var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(ct);
        Requests.Add(new RecordedRequest(
            request.Method,
            request.RequestUri!,
            request.Headers.Authorization?.ToString(),
            request.Content?.Headers.ContentType?.ToString(),
            body));
        foreach (var (match, responses) in _routes)
        {
            if (match(request) && responses.Count > 0)
            {
                var response = responses.Count == 1 ? responses.Peek()() : responses.Dequeue()();
                return response;
            }
        }
        throw new InvalidOperationException($"no scripted response for {request.Method} {request.RequestUri}");
    }

    public static HttpResponseMessage Json(HttpStatusCode status, string json, Action<HttpResponseMessage>? mutate = null)
    {
        var response = new HttpResponseMessage(status)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json"),
        };
        mutate?.Invoke(response);
        return response;
    }
}

public sealed class ArmLroFixture
{
    public const string Tenant = "70a0e4f1-ae94-44a5-8d4f-1c4e59c1c9f7";
    public const string ClientId = "9f6b1c2d-0d3e-4b6a-9c1e-2f4a5b6c7d8e";
    public const string ClientSecret = "dummy-secret-not-a-real-credential";
    public const string Scope = "https://management.azure.com/.default";
    public const string ResourcePath =
        "/subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/ops-tools/providers/Contoso.Compute/renderPools/pool7";
    public const string ApiVersion = "2024-11-01";
    public static readonly Uri TokenEndpoint =
        new($"https://login.microsoftonline.com/{Tenant}/oauth2/v2.0/token");
    public static readonly Uri ArmBase = new("https://management.azure.com");

    public ScriptedHandler Handler { get; } = new();
    public List<TimeSpan> Delays { get; } = new();
    public DateTimeOffset Now { get; set; } = new(2026, 7, 16, 12, 0, 0, TimeSpan.Zero);
    public int TokenCalls { get; private set; }

    public EntraTokenProvider Tokens { get; }
    public ArmLroClient Client { get; }

    public ArmLroFixture(int maxPolls = 20, int defaultPollSeconds = 5)
    {
        Handler.Enqueue(
            r => r.RequestUri == TokenEndpoint,
            () =>
            {
                TokenCalls++;
                return ScriptedHandler.Json(HttpStatusCode.OK,
                    $$"""{"token_type":"Bearer","expires_in":3600,"access_token":"tok-{{TokenCalls}}"}""");
            });
        var http = new HttpClient(Handler) { BaseAddress = ArmBase };
        Tokens = new EntraTokenProvider(http, TokenEndpoint, ClientId, ClientSecret, Scope, () => Now);
        var options = new ArmLroClientOptions
        {
            MaxPolls = maxPolls,
            DefaultPollDelay = TimeSpan.FromSeconds(defaultPollSeconds),
            Delay = (wait, _) => { Delays.Add(wait); return Task.CompletedTask; },
        };
        Client = new ArmLroClient(http, Tokens, options);
    }

    public List<RecordedRequest> ArmRequests()
        => Handler.Requests.Where(r => r.Uri != TokenEndpoint).ToList();
}

public class TokenProviderTests
{
    [Fact]
    public async Task Sends_documented_client_credentials_form()
    {
        var fx = new ArmLroFixture();
        var token = await fx.Tokens.GetTokenAsync();

        Assert.Equal("tok-1", token);
        var request = Assert.Single(fx.Handler.Requests);
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal(ArmLroFixture.TokenEndpoint, request.Uri);
        Assert.StartsWith("application/x-www-form-urlencoded", request.ContentType);

        var form = request.Body.Split('&')
            .Select(pair => pair.Split('=', 2))
            .ToDictionary(kv => kv[0], kv => Uri.UnescapeDataString(kv[1]));
        Assert.Equal("client_credentials", form["grant_type"]);
        Assert.Equal(ArmLroFixture.ClientId, form["client_id"]);
        Assert.Equal(ArmLroFixture.ClientSecret, form["client_secret"]);
        Assert.Equal(ArmLroFixture.Scope, form["scope"]);
    }

    [Fact]
    public async Task Caches_token_until_expiry_and_refreshes_after()
    {
        var fx = new ArmLroFixture();
        Assert.Equal("tok-1", await fx.Tokens.GetTokenAsync());
        Assert.Equal("tok-1", await fx.Tokens.GetTokenAsync());
        Assert.Equal(1, fx.TokenCalls);

        fx.Now = fx.Now.AddSeconds(3600 + 1);
        Assert.Equal("tok-2", await fx.Tokens.GetTokenAsync());
        Assert.Equal(2, fx.TokenCalls);
    }

    [Fact]
    public async Task Auth_failure_reports_error_code_but_never_the_secret()
    {
        var handler = new ScriptedHandler();
        handler.Enqueue(_ => true, () => ScriptedHandler.Json(HttpStatusCode.BadRequest,
            """{"error":"invalid_client","error_description":"AADSTS7000215: Invalid client secret provided."}"""));
        var provider = new EntraTokenProvider(
            new HttpClient(handler), ArmLroFixture.TokenEndpoint,
            ArmLroFixture.ClientId, ArmLroFixture.ClientSecret, ArmLroFixture.Scope,
            () => DateTimeOffset.UnixEpoch);

        var ex = await Assert.ThrowsAsync<ArmAuthException>(() => provider.GetTokenAsync());
        Assert.Contains("invalid_client", ex.Message);
        Assert.DoesNotContain(ArmLroFixture.ClientSecret, ex.Message);
        Assert.DoesNotContain(ArmLroFixture.ClientSecret, ex.ToString());
    }
}

public class SynchronousCompletionTests
{
    [Fact]
    public async Task Put_carries_api_version_bearer_and_json_body()
    {
        var fx = new ArmLroFixture();
        fx.Handler.Enqueue(
            r => r.Method == HttpMethod.Put,
            () => ScriptedHandler.Json(HttpStatusCode.OK,
                """{"id":"/x/pool7","name":"pool7","properties":{"provisioningState":"Succeeded","size":4}}"""));

        var body = """{"location":"westus2","properties":{"size":4}}""";
        var result = await fx.Client.CreateOrUpdateAsync(
            ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, body);

        var put = Assert.Single(fx.ArmRequests());
        Assert.Equal(HttpMethod.Put, put.Method);
        Assert.Equal("management.azure.com", put.Uri.Host);
        Assert.Equal(ArmLroFixture.ResourcePath, put.Uri.AbsolutePath);
        Assert.Contains($"api-version={ArmLroFixture.ApiVersion}", put.Uri.Query);
        Assert.Equal("Bearer tok-1", put.Authorization);
        Assert.StartsWith("application/json", put.ContentType);
        Assert.Equal(body, put.Body);

        Assert.True(result.Succeeded);
        Assert.Equal("Succeeded", result.Status);
        Assert.NotNull(result.Resource);
        Assert.Equal(4, result.Resource!.Value.GetProperty("properties").GetProperty("size").GetInt32());
        Assert.Empty(fx.Delays);
    }

    [Fact]
    public async Task Initial_rejection_maps_the_arm_error_body()
    {
        var fx = new ArmLroFixture();
        fx.Handler.Enqueue(
            r => r.Method == HttpMethod.Put,
            () => ScriptedHandler.Json(HttpStatusCode.BadRequest,
                """{"error":{"code":"InvalidTemplate","message":"size must be positive"}}""",
                resp => resp.Headers.Add("x-ms-error-code", "InvalidTemplate")));

        var ex = await Assert.ThrowsAsync<ArmRequestException>(() =>
            fx.Client.CreateOrUpdateAsync(ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}"));
        Assert.Equal(HttpStatusCode.BadRequest, ex.StatusCode);
        Assert.Equal("InvalidTemplate", ex.Error?.Code);
        Assert.Equal("size must be positive", ex.Error?.Message);
    }
}

public class MonitorPrecedenceTests
{
    private const string MonitorAao =
        "https://management.azure.com/subscriptions/11111111-2222-3333-4444-555555555555/providers/Contoso.Compute/locations/westus2/operations/op-aao?api-version=2024-11-01";
    private const string MonitorOpLoc =
        "https://management.azure.com/subscriptions/11111111-2222-3333-4444-555555555555/providers/Contoso.Compute/operations/op-oploc?api-version=2024-11-01";
    private const string MonitorLocation =
        "https://management.azure.com/subscriptions/11111111-2222-3333-4444-555555555555/providers/Contoso.Compute/operations/op-loc?monitor=true&api-version=2024-11-01";

    private static void EnqueueAccepted(ArmLroFixture fx, params (string Name, string Value)[] headers)
    {
        fx.Handler.Enqueue(
            r => r.Method == HttpMethod.Put,
            () => ScriptedHandler.Json(HttpStatusCode.Accepted, "", resp =>
            {
                foreach (var (name, value) in headers)
                {
                    resp.Headers.Add(name, value);
                }
            }));
    }

    [Fact]
    public async Task Azure_AsyncOperation_wins_over_both_other_headers()
    {
        var fx = new ArmLroFixture();
        EnqueueAccepted(fx,
            ("Azure-AsyncOperation", MonitorAao),
            ("Operation-Location", MonitorOpLoc),
            ("Location", MonitorLocation),
            ("Retry-After", "7"));
        fx.Handler.Enqueue(
            r => r.RequestUri!.AbsoluteUri == MonitorAao,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"InProgress"}"""),
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"Succeeded"}"""));
        fx.Handler.Enqueue(
            r => r.Method == HttpMethod.Get && r.RequestUri!.AbsolutePath == ArmLroFixture.ResourcePath,
            () => ScriptedHandler.Json(HttpStatusCode.OK,
                """{"name":"pool7","properties":{"provisioningState":"Succeeded","size":9}}"""));

        var result = await fx.Client.CreateOrUpdateAsync(
            ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, """{"properties":{"size":9}}""");

        var polled = fx.ArmRequests().Where(r => r.Method == HttpMethod.Get).ToList();
        Assert.All(polled.Take(2), r => Assert.Equal(MonitorAao, r.Uri.AbsoluteUri));
        Assert.DoesNotContain(fx.ArmRequests(), r => r.Uri.AbsoluteUri == MonitorOpLoc);
        Assert.DoesNotContain(fx.ArmRequests(), r => r.Uri.AbsoluteUri == MonitorLocation);

        // Final resource comes from a fresh GET of the resource URL with the same api-version.
        var finalGet = polled.Last();
        Assert.Equal(ArmLroFixture.ResourcePath, finalGet.Uri.AbsolutePath);
        Assert.Contains($"api-version={ArmLroFixture.ApiVersion}", finalGet.Uri.Query);
        Assert.Equal("Bearer tok-1", finalGet.Authorization);

        Assert.True(result.Succeeded);
        Assert.Equal(9, result.Resource!.Value.GetProperty("properties").GetProperty("size").GetInt32());
    }

    [Fact]
    public async Task Operation_Location_wins_when_AzureAsyncOperation_is_absent()
    {
        var fx = new ArmLroFixture();
        EnqueueAccepted(fx,
            ("Operation-Location", MonitorOpLoc),
            ("Location", MonitorLocation));
        fx.Handler.Enqueue(
            r => r.RequestUri!.AbsoluteUri == MonitorOpLoc,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"Succeeded"}"""));
        fx.Handler.Enqueue(
            r => r.Method == HttpMethod.Get && r.RequestUri!.AbsolutePath == ArmLroFixture.ResourcePath,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"name":"pool7","properties":{}}"""));

        var result = await fx.Client.CreateOrUpdateAsync(
            ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}");

        Assert.True(result.Succeeded);
        Assert.Contains(fx.ArmRequests(), r => r.Uri.AbsoluteUri == MonitorOpLoc);
        Assert.DoesNotContain(fx.ArmRequests(), r => r.Uri.AbsoluteUri == MonitorLocation);
    }

    [Fact]
    public async Task Location_monitor_returns_202_until_the_final_200_body()
    {
        var fx = new ArmLroFixture();
        EnqueueAccepted(fx, ("Location", MonitorLocation), ("Retry-After", "17"));
        fx.Handler.Enqueue(
            r => r.RequestUri!.AbsoluteUri == MonitorLocation,
            () => ScriptedHandler.Json(HttpStatusCode.Accepted, "", resp => resp.Headers.Add("Retry-After", "3")),
            () => ScriptedHandler.Json(HttpStatusCode.OK,
                """{"name":"pool7","properties":{"provisioningState":"Succeeded","tier":"premium"}}"""));

        var result = await fx.Client.CreateOrUpdateAsync(
            ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}");

        Assert.True(result.Succeeded);
        // The 200 body from the Location URL IS the final resource; no extra GET of the resource path.
        Assert.Equal("premium", result.Resource!.Value.GetProperty("properties").GetProperty("tier").GetString());
        var gets = fx.ArmRequests().Where(r => r.Method == HttpMethod.Get).ToList();
        Assert.Equal(2, gets.Count);
        Assert.All(gets, r => Assert.Equal(MonitorLocation, r.Uri.AbsoluteUri));
        Assert.Equal(new[] { TimeSpan.FromSeconds(17), TimeSpan.FromSeconds(3) }, fx.Delays);
    }

    [Fact]
    public async Task Accepted_with_no_monitor_header_is_a_protocol_error()
    {
        var fx = new ArmLroFixture();
        EnqueueAccepted(fx);
        await Assert.ThrowsAsync<ArmProtocolException>(() =>
            fx.Client.CreateOrUpdateAsync(ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}"));
    }

    [Fact]
    public async Task Monitor_on_a_foreign_host_never_receives_the_bearer_token()
    {
        var fx = new ArmLroFixture();
        EnqueueAccepted(fx, ("Azure-AsyncOperation", "https://collector.example.net/operations/op1"));
        var ex = await Assert.ThrowsAsync<ArmProtocolException>(() =>
            fx.Client.CreateOrUpdateAsync(ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}"));
        Assert.Contains("collector.example.net", ex.Message);
        // The untrusted URL must not have been called at all, with or without credentials.
        Assert.DoesNotContain(fx.Handler.Requests, r => r.Uri.Host == "collector.example.net");
    }
}

public class PollingBehaviorTests
{
    private const string Monitor =
        "https://management.azure.com/subscriptions/11111111-2222-3333-4444-555555555555/providers/Contoso.Compute/locations/westus2/operations/op-poll?api-version=2024-11-01";

    private static ArmLroFixture WithMonitor(int maxPolls, params Func<HttpResponseMessage>[] monitorResponses)
    {
        var fx = new ArmLroFixture(maxPolls: maxPolls, defaultPollSeconds: 11);
        fx.Handler.Enqueue(
            r => r.Method == HttpMethod.Put,
            () => ScriptedHandler.Json(HttpStatusCode.Accepted, "", resp =>
            {
                resp.Headers.Add("Azure-AsyncOperation", Monitor);
                resp.Headers.Add("Retry-After", "2");
            }));
        fx.Handler.Enqueue(r => r.RequestUri!.AbsoluteUri == Monitor, monitorResponses);
        return fx;
    }

    [Fact]
    public async Task Retry_After_is_honored_per_response_with_default_fallback()
    {
        var fx = WithMonitor(20,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"InProgress"}""",
                resp => resp.Headers.Add("Retry-After", "29")),
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"Running"}"""),
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"Succeeded"}"""));
        fx.Handler.Enqueue(
            r => r.Method == HttpMethod.Get && r.RequestUri!.AbsolutePath == ArmLroFixture.ResourcePath,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"name":"pool7"}"""));

        var result = await fx.Client.CreateOrUpdateAsync(
            ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}");

        Assert.True(result.Succeeded);
        // 2s from the initial 202, 29s from the first poll, then the 11s default.
        Assert.Equal(
            new[] { TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(29), TimeSpan.FromSeconds(11) },
            fx.Delays);
    }

    [Fact]
    public async Task Provider_specific_running_states_keep_polling_and_terminal_match_is_case_insensitive()
    {
        var fx = WithMonitor(20,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"MigratingData"}"""),
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"succeeded"}"""));
        fx.Handler.Enqueue(
            r => r.Method == HttpMethod.Get && r.RequestUri!.AbsolutePath == ArmLroFixture.ResourcePath,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"name":"pool7"}"""));

        var result = await fx.Client.CreateOrUpdateAsync(
            ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}");
        Assert.True(result.Succeeded);
        Assert.Equal("succeeded", result.Status);
    }

    [Fact]
    public async Task Failed_operation_returns_the_structured_error()
    {
        var fx = WithMonitor(20,
            () => ScriptedHandler.Json(HttpStatusCode.OK,
                """{"status":"Failed","error":{"code":"AllocationFailed","message":"zone capacity exhausted"}}"""));

        var result = await fx.Client.CreateOrUpdateAsync(
            ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}");

        Assert.False(result.Succeeded);
        Assert.Equal("Failed", result.Status);
        Assert.Null(result.Resource);
        Assert.Equal("AllocationFailed", result.Error?.Code);
        Assert.Equal("zone capacity exhausted", result.Error?.Message);
        // No final resource GET after a failure.
        Assert.DoesNotContain(fx.ArmRequests(),
            r => r.Method == HttpMethod.Get && r.Uri.AbsolutePath == ArmLroFixture.ResourcePath);
    }

    [Fact]
    public async Task Canceled_is_terminal_with_error_details()
    {
        var fx = WithMonitor(20,
            () => ScriptedHandler.Json(HttpStatusCode.OK,
                """{"status":"Canceled","error":{"code":"OperationCanceled","message":"canceled by operator"}}"""));

        var result = await fx.Client.CreateOrUpdateAsync(
            ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}");
        Assert.False(result.Succeeded);
        Assert.Equal("Canceled", result.Status);
        Assert.Equal("OperationCanceled", result.Error?.Code);
    }

    [Fact]
    public async Task Monitor_body_without_status_is_a_protocol_error()
    {
        var fx = WithMonitor(20,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"percentComplete":50}"""));
        await Assert.ThrowsAsync<ArmProtocolException>(() =>
            fx.Client.CreateOrUpdateAsync(ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}"));
    }

    [Fact]
    public async Task Poll_budget_is_bounded()
    {
        var fx = WithMonitor(3,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"InProgress"}"""));
        var ex = await Assert.ThrowsAsync<ArmProtocolException>(() =>
            fx.Client.CreateOrUpdateAsync(ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}"));
        Assert.Equal(3, fx.ArmRequests().Count(r => r.Method == HttpMethod.Get));
    }

    [Fact]
    public async Task Token_is_acquired_once_across_the_whole_operation()
    {
        var fx = WithMonitor(20,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"InProgress"}"""),
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"status":"Succeeded"}"""));
        fx.Handler.Enqueue(
            r => r.Method == HttpMethod.Get && r.RequestUri!.AbsolutePath == ArmLroFixture.ResourcePath,
            () => ScriptedHandler.Json(HttpStatusCode.OK, """{"name":"pool7"}"""));

        await fx.Client.CreateOrUpdateAsync(ArmLroFixture.ResourcePath, ArmLroFixture.ApiVersion, "{}");

        Assert.Equal(1, fx.TokenCalls);
        Assert.All(fx.ArmRequests(), r => Assert.Equal("Bearer tok-1", r.Authorization));
    }
}
