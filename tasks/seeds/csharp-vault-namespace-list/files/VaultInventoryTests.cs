// Acceptance harness: a loopback fake Vault server (raw TCP so the LIST HTTP
// verb arrives untouched) exercising the wire contract pinned in
// docs/contract.json. No real Vault, no real credentials.
// Protected — do not modify. Run: dotnet test

using System.Net;
using System.Net.Sockets;
using System.Text;

namespace VaultNamespaceList.Tests;

public sealed record RecordedRequest(string Method, string Path, string Query, string? Token, string? Namespace);

public sealed class FakeVault : IDisposable
{
    private readonly TcpListener _listener;
    private readonly object _lock = new();
    private readonly List<RecordedRequest> _requests = new();
    private readonly Dictionary<string, (int Status, string Body)> _routes = new();

    public FakeVault()
    {
        _listener = new TcpListener(IPAddress.Loopback, 0);
        _listener.Start();
        BaseUrl = $"http://127.0.0.1:{((IPEndPoint)_listener.LocalEndpoint).Port}";
        _ = Task.Run(AcceptLoopAsync);
    }

    public string BaseUrl { get; }

    public IReadOnlyList<RecordedRequest> Requests
    {
        get { lock (_lock) return _requests.ToList(); }
    }

    /// Routes are keyed by (namespace header value or "", URL path). Anything
    /// unrouted answers Vault's empty-list document: 404 {"errors":[]}.
    public void Route(string ns, string path, int status, string body)
    {
        lock (_lock) _routes[ns + "|" + path] = (status, body);
    }

    public static string Keys(params string[] keys) =>
        "{\"data\":{\"keys\":[" + string.Join(",", keys.Select(k => "\"" + k + "\"")) + "]}}";

    private async Task AcceptLoopAsync()
    {
        while (true)
        {
            TcpClient client;
            try { client = await _listener.AcceptTcpClientAsync(); }
            catch (Exception) { return; } // listener disposed
            _ = Task.Run(() => HandleAsync(client));
        }
    }

    private async Task HandleAsync(TcpClient client)
    {
        using (client)
        {
            var stream = client.GetStream();
            var buffer = new byte[16384];
            var have = 0;
            while (true)
            {
                var n = await stream.ReadAsync(buffer.AsMemory(have, buffer.Length - have));
                if (n == 0) return;
                have += n;
                if (Encoding.ASCII.GetString(buffer, 0, have).Contains("\r\n\r\n")) break;
            }
            var text = Encoding.ASCII.GetString(buffer, 0, have);
            var lines = text.Split("\r\n");
            var requestLine = lines[0].Split(' ');
            var method = requestLine[0];
            var target = requestLine[1];
            var qIdx = target.IndexOf('?');
            var path = qIdx < 0 ? target : target[..qIdx];
            var query = qIdx < 0 ? "" : target[(qIdx + 1)..];
            string? token = null, ns = null;
            foreach (var line in lines.Skip(1))
            {
                if (line.Length == 0) break;
                var sep = line.IndexOf(':');
                if (sep < 0) continue;
                var name = line[..sep].Trim().ToLowerInvariant();
                var value = line[(sep + 1)..].Trim();
                if (name == "x-vault-token") token = value;
                if (name == "x-vault-namespace") ns = value;
            }

            (int Status, string Body) resp;
            lock (_lock)
            {
                _requests.Add(new RecordedRequest(method, path, query, token, ns));
                if (!_routes.TryGetValue((ns ?? "") + "|" + path, out resp))
                    resp = (404, "{\"errors\":[]}");
            }

            var payload = Encoding.UTF8.GetBytes(resp.Body);
            var reason = resp.Status switch
            {
                200 => "OK",
                403 => "Forbidden",
                404 => "Not Found",
                _ => "Status",
            };
            var head = $"HTTP/1.1 {resp.Status} {reason}\r\n" +
                       "Content-Type: application/json\r\n" +
                       $"Content-Length: {payload.Length}\r\n" +
                       "Connection: close\r\n\r\n";
            await stream.WriteAsync(Encoding.ASCII.GetBytes(head));
            await stream.WriteAsync(payload);
            await stream.FlushAsync();
        }
    }

    public void Dispose() => _listener.Stop();
}

public class VaultInventoryTests : IDisposable
{
    private const string Token = "hvs.dummy-inventory-7733"; // dummy credential

    private readonly FakeVault _fake = new();
    private readonly HttpClient _http = new();

    public void Dispose()
    {
        _fake.Dispose();
        _http.Dispose();
    }

    private VaultListClient Client(string? ns = "eng/", bool fallback = false) =>
        new(_http, new Uri(_fake.BaseUrl), Token, ns, fallback);

    private static void AssertIsListRequest(RecordedRequest r, bool fallback)
    {
        if (fallback)
        {
            Assert.Equal("GET", r.Method);
            Assert.Equal("list=true", r.Query);
        }
        else
        {
            Assert.Equal("LIST", r.Method);
            Assert.Equal("", r.Query);
        }
    }

    [Fact]
    public async Task ListUsesTheListVerbAndVaultHeaders()
    {
        _fake.Route("eng/", "/v1/secret/metadata/app", 200, FakeVault.Keys("config", "db/"));

        var keys = await Client().ListKeysAsync("secret/metadata/app");

        Assert.Equal(new[] { "config", "db/" }, keys);
        var r = Assert.Single(_fake.Requests);
        AssertIsListRequest(r, fallback: false);
        Assert.Equal("/v1/secret/metadata/app", r.Path);
        Assert.Equal(Token, r.Token);
        Assert.Equal("eng/", r.Namespace);
        Assert.DoesNotContain(Token, r.Query);
    }

    [Fact]
    public async Task FallbackModeUsesGetWithListTrue()
    {
        _fake.Route("eng/", "/v1/secret/metadata/app", 200, FakeVault.Keys("config", "db/"));

        var keys = await Client(fallback: true).ListKeysAsync("secret/metadata/app");

        Assert.Equal(new[] { "config", "db/" }, keys);
        var r = Assert.Single(_fake.Requests);
        AssertIsListRequest(r, fallback: true);
        Assert.Equal("/v1/secret/metadata/app", r.Path);
        Assert.Equal(Token, r.Token);
    }

    [Fact]
    public async Task EmptyList404WithEmptyErrorsIsAnEmptyResult()
    {
        // No route registered: the fake answers 404 {"errors":[]}, which is
        // Vault's documented empty-list response — NOT an error.
        var keys = await Client().ListKeysAsync("secret/metadata/nothing-here");
        Assert.Empty(keys);

        var fallbackKeys = await Client(fallback: true).ListKeysAsync("secret/metadata/nothing-here");
        Assert.Empty(fallbackKeys);
    }

    [Fact]
    public async Task NonEmptyErrorsPropagateWithStatusAndMessages()
    {
        _fake.Route("eng/", "/v1/secret/metadata/forbidden", 403,
            "{\"errors\":[\"1 error occurred:\\n\\t* permission denied\\n\\n\"]}");
        _fake.Route("eng/", "/v1/nosuch/metadata", 404,
            "{\"errors\":[\"no handler for route \\\"nosuch/metadata\\\". route entry not found.\"]}");

        var forbidden = await Assert.ThrowsAsync<VaultApiException>(
            () => Client().ListKeysAsync("secret/metadata/forbidden"));
        Assert.Equal(403, forbidden.StatusCode);
        var msg = Assert.Single(forbidden.Errors);
        Assert.Contains("permission denied", msg);
        Assert.Contains("403", forbidden.Message);

        // A 404 whose errors array is NON-empty is a real error, not an empty list.
        var notFound = await Assert.ThrowsAsync<VaultApiException>(
            () => Client().ListKeysAsync("nosuch/metadata"));
        Assert.Equal(404, notFound.StatusCode);
        Assert.Contains("route entry not found", Assert.Single(notFound.Errors));
    }

    [Fact]
    public async Task WalkDescendsFoldersAndReturnsSortedLeafPaths()
    {
        // "app" is both a secret and a folder — Vault reports both entries.
        _fake.Route("eng/", "/v1/secret/metadata", 200, FakeVault.Keys("app", "app/", "ops/", "top"));
        _fake.Route("eng/", "/v1/secret/metadata/app", 200, FakeVault.Keys("config", "db/"));
        _fake.Route("eng/", "/v1/secret/metadata/app/db", 200, FakeVault.Keys("creds"));
        _fake.Route("eng/", "/v1/secret/metadata/ops", 200, FakeVault.Keys("runbook"));

        var paths = await Client().WalkSecretPathsAsync("secret");

        Assert.Equal(new[] { "app", "app/config", "app/db/creds", "ops/runbook", "top" }, paths);

        var requested = _fake.Requests.Select(r => r.Path).ToHashSet();
        Assert.Equal(
            new HashSet<string>
            {
                "/v1/secret/metadata",
                "/v1/secret/metadata/app",
                "/v1/secret/metadata/app/db",
                "/v1/secret/metadata/ops",
            },
            requested);
        Assert.All(_fake.Requests, r => AssertIsListRequest(r, fallback: false));
    }

    [Fact]
    public async Task WalkTreatsEmptyFolder404AsNoChildren()
    {
        _fake.Route("eng/", "/v1/secret/metadata", 200, FakeVault.Keys("app/", "solo"));
        // /v1/secret/metadata/app is unrouted -> 404 {"errors":[]} mid-walk.

        var paths = await Client().WalkSecretPathsAsync("secret");

        Assert.Equal(new[] { "solo" }, paths);
    }

    [Fact]
    public async Task ListNamespacesParsesKeyInfo()
    {
        _fake.Route("eng/", "/v1/sys/namespaces", 200,
            "{\"data\":{\"key_info\":{" +
            "\"team-b/\":{\"custom_metadata\":{},\"id\":\"ns_bB2\",\"path\":\"team-b/\"}," +
            "\"team-a/\":{\"custom_metadata\":{},\"id\":\"ns_aA1\",\"path\":\"team-a/\"}}," +
            "\"keys\":[\"team-b/\",\"team-a/\"]}}");

        var namespaces = await Client().ListNamespacesAsync();

        Assert.Equal(
            new[] { new VaultNamespaceInfo("team-a/", "ns_aA1"), new VaultNamespaceInfo("team-b/", "ns_bB2") },
            namespaces);
        var r = Assert.Single(_fake.Requests);
        Assert.Equal("/v1/sys/namespaces", r.Path);
        Assert.Equal("eng/", r.Namespace);
        AssertIsListRequest(r, fallback: false);
    }

    [Fact]
    public async Task ListNamespacesEmpty404MeansNoChildren()
    {
        var namespaces = await Client().ListNamespacesAsync();
        Assert.Empty(namespaces);
    }

    [Fact]
    public async Task InventoryRecursesChildNamespacesWithAbsoluteHeaderPaths()
    {
        _fake.Route("eng/", "/v1/sys/namespaces", 200,
            "{\"data\":{\"key_info\":{" +
            "\"team-a/\":{\"id\":\"ns_aA1\",\"path\":\"team-a/\"}," +
            "\"team-b/\":{\"id\":\"ns_bB2\",\"path\":\"team-b/\"}}," +
            "\"keys\":[\"team-a/\",\"team-b/\"]}}");
        _fake.Route("eng/", "/v1/secret/metadata", 200, FakeVault.Keys("shared"));
        // team-a has secrets but no children (sys/namespaces unrouted -> empty 404).
        _fake.Route("eng/team-a/", "/v1/secret/metadata", 200, FakeVault.Keys("alpha"));
        // team-b has one nested child namespace.
        _fake.Route("eng/team-b/", "/v1/sys/namespaces", 200,
            "{\"data\":{\"key_info\":{\"blue/\":{\"id\":\"ns_cC3\",\"path\":\"blue/\"}},\"keys\":[\"blue/\"]}}");
        _fake.Route("eng/team-b/", "/v1/secret/metadata", 200, FakeVault.Keys("beta"));
        _fake.Route("eng/team-b/blue/", "/v1/secret/metadata", 200, FakeVault.Keys("gamma"));

        var entries = await Client().InventoryAsync("secret", includeChildNamespaces: true);

        Assert.Equal(
            new[]
            {
                new VaultSecretRef("eng/", "shared"),
                new VaultSecretRef("eng/team-a/", "alpha"),
                new VaultSecretRef("eng/team-b/", "beta"),
                new VaultSecretRef("eng/team-b/blue/", "gamma"),
            },
            entries);

        // Namespace targeting must be consistent: the child path from
        // sys/namespaces is appended to the configured namespace header.
        var secretListNamespaces = _fake.Requests
            .Where(r => r.Path == "/v1/secret/metadata")
            .Select(r => r.Namespace)
            .ToHashSet();
        Assert.Equal(new HashSet<string?> { "eng/", "eng/team-a/", "eng/team-b/", "eng/team-b/blue/" },
            secretListNamespaces);
        var nsListNamespaces = _fake.Requests
            .Where(r => r.Path == "/v1/sys/namespaces")
            .Select(r => r.Namespace)
            .ToHashSet();
        Assert.Equal(new HashSet<string?> { "eng/", "eng/team-a/", "eng/team-b/", "eng/team-b/blue/" },
            nsListNamespaces);
        Assert.All(_fake.Requests, r => Assert.Equal(Token, r.Token));
    }

    [Fact]
    public async Task InventoryWithoutRecursionStaysInTheConfiguredNamespace()
    {
        _fake.Route("eng/", "/v1/secret/metadata", 200, FakeVault.Keys("shared"));

        var entries = await Client().InventoryAsync("secret", includeChildNamespaces: false);

        Assert.Equal(new[] { new VaultSecretRef("eng/", "shared") }, entries);
        Assert.DoesNotContain(_fake.Requests, r => r.Path == "/v1/sys/namespaces");
    }

    [Fact]
    public async Task RootNamespaceClientOmitsTheHeaderEntirely()
    {
        _fake.Route("", "/v1/sys/namespaces", 200,
            "{\"data\":{\"key_info\":{\"team-x/\":{\"id\":\"ns_x9\",\"path\":\"team-x/\"}},\"keys\":[\"team-x/\"]}}");
        _fake.Route("", "/v1/secret/metadata", 200, FakeVault.Keys("root-secret"));
        _fake.Route("team-x/", "/v1/secret/metadata", 200, FakeVault.Keys("xray"));

        var entries = await Client(ns: null).InventoryAsync("secret", includeChildNamespaces: true);

        Assert.Equal(
            new[] { new VaultSecretRef("", "root-secret"), new VaultSecretRef("team-x/", "xray") },
            entries);
        var rootRequests = _fake.Requests.Where(r => r.Namespace is null).ToList();
        Assert.Equal(2, rootRequests.Count); // root sys/namespaces + root secret list
        var childSecretList = _fake.Requests.Single(r => r.Path == "/v1/secret/metadata" && r.Namespace != null);
        Assert.Equal("team-x/", childSecretList.Namespace);
    }
}
