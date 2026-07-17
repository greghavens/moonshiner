// Acceptance tests for the Microsoft Graph v1.0 user delta synchronizer.
//
// Runs a loopback fake of the Graph users/delta endpoint implementing the
// contract pinned in docs/contract.json: opaque $skiptoken/$deltatoken links,
// @removed tombstones, 429 Retry-After throttling and 410 sync reset.
// No vendor network, no real credentials.

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using GraphDeltaSync;

namespace GraphDeltaSyncTests;

public sealed class FakeGraphDirectory : IDisposable
{
    public const string Token = "dummy-token-8f31ac"; // dummy; must never leak

    public const string InitQuery = "$select=id,displayName,mail,jobTitle";
    public const string Skip1 = "$skiptoken=pg2-kJhbGciOiJIUzI1NiJ9%3D%3D";
    public const string Skip2 = "$skiptoken=pg3-mQ0NTY3ODkwIiwibmFtZQ%3D";
    public const string Delta1 = "$deltatoken=rd1-YWRlbGUudmFuY2U0NTY3";
    public const string Skip3 = "$skiptoken=pg5-c3luY19zdGF0ZV9wYWdlMg%3D%3D";
    public const string Delta2 = "$deltatoken=rd2-bmV4dF9yb3VuZF90b2tlbg%3D%3D";
    public const string ResyncQuery = "$deltatoken=";
    public const string SkipR = "$skiptoken=rs1-ZnVsbF9yZXN5bmNfcGcy";
    public const string Delta3 = "$deltatoken=rd3-YWZ0ZXJfcmVzeW5j";

    public sealed record Recorded(string Method, string Path, string RawQuery, string? Auth);

    public sealed record Fault(int Status, int RetryAfter = 0, string? Location = null,
        string ErrorCode = "TooManyRequests", string Message = "Please retry again later.");

    private sealed record Page(List<Dictionary<string, object?>> Items, string? NextQuery, string? DeltaQuery);

    public List<Recorded> Requests { get; } = new();
    public string BaseUrl { get; }
    public string GraphBase => BaseUrl + "/v1.0";

    private readonly Dictionary<string, Page> _script = new();
    private readonly Dictionary<string, Queue<Fault>> _faults = new();
    private readonly Dictionary<string, Fault> _alwaysFaults = new();
    private readonly HttpListener _listener;
    private readonly Task _loop;

    public FakeGraphDirectory()
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

    public static Dictionary<string, object?> User(string id, string? displayName, string? mail, string? jobTitle) =>
        new()
        {
            ["id"] = id,
            ["displayName"] = displayName,
            ["mail"] = mail,
            ["jobTitle"] = jobTitle,
        };

    public static Dictionary<string, object?> Tombstone(string id, string reason) =>
        new()
        {
            ["id"] = id,
            ["@removed"] = new Dictionary<string, object?> { ["reason"] = reason },
        };

    public void AddPage(string query, IEnumerable<Dictionary<string, object?>> items,
        string? nextQuery = null, string? deltaQuery = null)
    {
        _script[query] = new Page(items.ToList(), nextQuery, deltaQuery);
    }

    public void QueueFault(string query, Fault fault)
    {
        if (!_faults.TryGetValue(query, out var q))
            _faults[query] = q = new Queue<Fault>();
        q.Enqueue(fault);
    }

    public void AlwaysFault(string query, Fault fault) => _alwaysFaults[query] = fault;

    public int CountRequests(string query) => Requests.Count(r => r.RawQuery == query);

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
                TryRespond(ctx, 500, ErrorBody("InternalServerError", "mock failure: " + ex.Message));
            }
        }
    }

    private static string ErrorBody(string code, string message) =>
        JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["error"] = new Dictionary<string, object?> { ["code"] = code, ["message"] = message },
        });

    private static void TryRespond(HttpListenerContext ctx, int status, string body,
        Dictionary<string, string>? headers = null)
    {
        try
        {
            byte[] bytes = Encoding.UTF8.GetBytes(body);
            ctx.Response.StatusCode = status;
            ctx.Response.ContentType = "application/json";
            if (headers is not null)
                foreach (var (k, v) in headers) ctx.Response.AddHeader(k, v);
            ctx.Response.ContentLength64 = bytes.Length;
            ctx.Response.OutputStream.Write(bytes);
            ctx.Response.Close();
        }
        catch (Exception) { /* client went away */ }
    }

    // Tolerate legal percent-encodings of the OData literals themselves, but
    // never of the opaque token bytes: verbatim link reuse is the contract.
    private static string NormalizeQuery(string rawQuery)
    {
        string q = rawQuery.StartsWith('?') ? rawQuery[1..] : rawQuery;
        return q.Replace("%24select", "$select").Replace("%24skiptoken", "$skiptoken")
                .Replace("%24deltatoken", "$deltatoken").Replace("%2C", ",").Replace("%2c", ",");
    }

    private void Handle(HttpListenerContext ctx)
    {
        var req = ctx.Request;
        string rawQuery = NormalizeQuery(req.Url?.Query ?? "");
        string path = req.Url?.AbsolutePath ?? "";
        string? auth = req.Headers["Authorization"];
        Requests.Add(new Recorded(req.HttpMethod, path, rawQuery, auth));

        if (auth != "Bearer " + Token)
        {
            TryRespond(ctx, 401, ErrorBody("InvalidAuthenticationToken", "Access token is empty."));
            return;
        }
        if (req.HttpMethod != "GET" || path != "/v1.0/users/delta")
        {
            TryRespond(ctx, 404, ErrorBody("ResourceNotFound", "No such endpoint: " + path));
            return;
        }

        Fault? fault = null;
        if (_alwaysFaults.TryGetValue(rawQuery, out var af)) fault = af;
        else if (_faults.TryGetValue(rawQuery, out var fq) && fq.Count > 0) fault = fq.Dequeue();
        if (fault is not null)
        {
            var headers = new Dictionary<string, string>();
            if (fault.Status == 429) headers["Retry-After"] = fault.RetryAfter.ToString();
            if (fault.Location is not null) headers["Location"] = fault.Location;
            TryRespond(ctx, fault.Status, ErrorBody(fault.ErrorCode, fault.Message), headers);
            return;
        }

        if (!_script.TryGetValue(rawQuery, out var page))
        {
            TryRespond(ctx, 400, ErrorBody("BadRequest",
                "Unrecognized state token; links must be used verbatim. Got query: " + rawQuery));
            return;
        }

        var body = new Dictionary<string, object?>
        {
            ["@odata.context"] = "https://graph.microsoft.com/v1.0/$metadata#users",
            ["value"] = page.Items,
        };
        if (page.NextQuery is not null)
            body["@odata.nextLink"] = GraphBase + "/users/delta?" + page.NextQuery;
        if (page.DeltaQuery is not null)
            body["@odata.deltaLink"] = GraphBase + "/users/delta?" + page.DeltaQuery;
        TryRespond(ctx, 200, JsonSerializer.Serialize(body));
    }

    public void Dispose()
    {
        try { _listener.Stop(); } catch (Exception) { }
        try { _loop.Wait(TimeSpan.FromSeconds(2)); } catch (Exception) { }
    }
}

public class DeltaSyncTests
{
    private const string U1 = "87d349ed-44d7-43e1-9a83-5f2406dee501";
    private const string U2 = "48d31887-5fad-4d73-a9f5-3c356e68a502";
    private const string U3 = "e3d0513b-449e-4198-ba6f-bd97ae7f6503";
    private const string U4 = "5bde3e51-d13b-4db1-9948-fe4b109d6504";
    private const string U5 = "6e7b768e-07e2-4810-8459-485f84f86505";
    private const string U6 = "9f427d3e-0c9e-4a2b-8c15-1d2f3a4b6506";
    private const string U9 = "00000000-dead-beef-0000-000000000509";

    private static FakeGraphDirectory NewFakeWithRoundA()
    {
        var fake = new FakeGraphDirectory();
        fake.AddPage(FakeGraphDirectory.InitQuery, new[]
        {
            FakeGraphDirectory.User(U1, "Adele Vance", "AdeleV@contoso.example", "Retail Manager"),
            FakeGraphDirectory.User(U2, "Alex Wilber", "AlexW@contoso.example", "Marketing Assistant"),
        }, nextQuery: FakeGraphDirectory.Skip1);
        fake.AddPage(FakeGraphDirectory.Skip1, new[]
        {
            FakeGraphDirectory.User(U3, "Bianca Pisani", null, null),
            FakeGraphDirectory.User(U4, "Diego Siciliani", "DiegoS@contoso.example", "HR Specialist"),
            FakeGraphDirectory.Tombstone(U9, "deleted"),
        }, nextQuery: FakeGraphDirectory.Skip2);
        fake.AddPage(FakeGraphDirectory.Skip2, new[]
        {
            FakeGraphDirectory.User(U5, "Emily Braun", "EmilyB@contoso.example", "Budget Analyst"),
        }, deltaQuery: FakeGraphDirectory.Delta1);
        return fake;
    }

    private static void AddRoundB(FakeGraphDirectory fake)
    {
        fake.AddPage(FakeGraphDirectory.Delta1, new[]
        {
            FakeGraphDirectory.User(U2, "Alex Wilber", "AlexW@contoso.example", "Marketing Manager"),
            FakeGraphDirectory.Tombstone(U4, "deleted"),
        }, nextQuery: FakeGraphDirectory.Skip3);
        fake.AddPage(FakeGraphDirectory.Skip3, new[]
        {
            FakeGraphDirectory.User(U6, "Grady Archie", "GradyA@contoso.example", "Designer"),
            FakeGraphDirectory.Tombstone(U3, "changed"),
            FakeGraphDirectory.Tombstone(U9, "deleted"),
        }, deltaQuery: FakeGraphDirectory.Delta2);
    }

    private static (UserDirectoryStore store, UserDeltaSynchronizer engine, List<double> delays)
        NewEngine(FakeGraphDirectory fake, int maxRetries = 3)
    {
        var store = new UserDirectoryStore();
        var delays = new List<double>();
        Func<TimeSpan, Task> delay = ts => { delays.Add(ts.TotalSeconds); return Task.CompletedTask; };
        var engine = new UserDeltaSynchronizer(fake.GraphBase, FakeGraphDirectory.Token, store, delay, maxRetries);
        return (store, engine, delays);
    }

    [Fact]
    public async Task InitialFullSync_FollowsNextLinksUntilDeltaLink()
    {
        using var fake = NewFakeWithRoundA();
        var (store, engine, _) = NewEngine(fake);

        SyncResult result = await engine.RunAsync();

        Assert.True(result.FullResync);
        Assert.Equal(3, result.Pages);
        Assert.Equal(5, result.UsersUpserted);
        Assert.Equal(0, result.UsersRemoved);
        Assert.Equal(5, store.Users.Count);
        Assert.Equal("Adele Vance", store.Users[U1].DisplayName);
        Assert.Equal("AdeleV@contoso.example", store.Users[U1].Mail);
        Assert.Equal("Retail Manager", store.Users[U1].JobTitle);
        Assert.Equal("Marketing Assistant", store.Users[U2].JobTitle);
        Assert.Null(store.Users[U3].Mail);
        Assert.Null(store.Users[U3].JobTitle);
        Assert.Equal("Diego Siciliani", store.Users[U4].DisplayName);
        Assert.Equal("Emily Braun", store.Users[U5].DisplayName);
        Assert.False(store.Users.ContainsKey(U9));

        string wantDelta = fake.GraphBase + "/users/delta?" + FakeGraphDirectory.Delta1;
        Assert.Equal(wantDelta, store.DeltaLink);
        Assert.Equal(wantDelta, result.DeltaLink);
    }

    [Fact]
    public async Task Requests_CarryBearerAuth_AndUseLinksVerbatim()
    {
        using var fake = NewFakeWithRoundA();
        var (_, engine, _) = NewEngine(fake);

        await engine.RunAsync();

        Assert.Equal(3, fake.Requests.Count);
        Assert.All(fake.Requests, r => Assert.Equal("Bearer " + FakeGraphDirectory.Token, r.Auth));
        Assert.All(fake.Requests, r => Assert.Equal("/v1.0/users/delta", r.Path));
        Assert.Equal(FakeGraphDirectory.InitQuery, fake.Requests[0].RawQuery);
        Assert.Equal(FakeGraphDirectory.Skip1, fake.Requests[1].RawQuery);
        Assert.Equal(FakeGraphDirectory.Skip2, fake.Requests[2].RawQuery);
    }

    [Fact]
    public async Task IncrementalRound_AppliesUpdatesAndTombstones()
    {
        using var fake = NewFakeWithRoundA();
        AddRoundB(fake);
        var (store, engine, _) = NewEngine(fake);

        await engine.RunAsync();
        SyncResult second = await engine.RunAsync();

        Assert.False(second.FullResync);
        Assert.Equal(2, second.Pages);
        Assert.Equal(2, second.UsersUpserted);
        Assert.Equal(2, second.UsersRemoved);

        Assert.Equal(4, store.Users.Count);
        Assert.Equal("Marketing Manager", store.Users[U2].JobTitle);
        Assert.Equal("Grady Archie", store.Users[U6].DisplayName);
        Assert.False(store.Users.ContainsKey(U3));
        Assert.False(store.Users.ContainsKey(U4));
        Assert.True(store.Users.ContainsKey(U1));
        Assert.True(store.Users.ContainsKey(U5));

        Assert.Equal(fake.GraphBase + "/users/delta?" + FakeGraphDirectory.Delta2, store.DeltaLink);

        // The incremental round must have started from the stored deltaLink,
        // and the follow-up page from the verbatim nextLink.
        Assert.Equal(1, fake.CountRequests(FakeGraphDirectory.Delta1));
        Assert.Equal(1, fake.CountRequests(FakeGraphDirectory.Skip3));
    }

    [Fact]
    public async Task Throttled_WaitsRetryAfter_ThenRepeatsSameUrl()
    {
        using var fake = NewFakeWithRoundA();
        AddRoundB(fake);
        fake.QueueFault(FakeGraphDirectory.Delta1, new FakeGraphDirectory.Fault(429, RetryAfter: 7));
        var (store, engine, delays) = NewEngine(fake);

        await engine.RunAsync();
        delays.Clear();
        SyncResult second = await engine.RunAsync();

        Assert.Equal(new[] { 7.0 }, delays);
        Assert.Equal(2, fake.CountRequests(FakeGraphDirectory.Delta1));
        Assert.Equal(2, second.Pages);
        Assert.Equal(4, store.Users.Count);
    }

    [Fact]
    public async Task ThrottleMidRound_ExhaustsRetries_LeavesStoreUntouched()
    {
        using var fake = NewFakeWithRoundA();
        AddRoundB(fake);
        fake.AlwaysFault(FakeGraphDirectory.Skip3, new FakeGraphDirectory.Fault(429, RetryAfter: 5));
        var (store, engine, delays) = NewEngine(fake);

        await engine.RunAsync();
        var before = store.Users.ToDictionary(kv => kv.Key, kv => kv.Value);
        string? deltaBefore = store.DeltaLink;
        delays.Clear();

        var ex = await Assert.ThrowsAsync<GraphThrottledException>(() => engine.RunAsync());

        Assert.Equal(5, ex.RetryAfterSeconds);
        Assert.Equal(429, ex.StatusCode);
        Assert.DoesNotContain(FakeGraphDirectory.Token, ex.Message);
        Assert.Equal(new[] { 5.0, 5.0, 5.0 }, delays);
        Assert.Equal(4, fake.CountRequests(FakeGraphDirectory.Skip3));

        // Aborted round: neither users nor deltaLink may have advanced.
        Assert.Equal(deltaBefore, store.DeltaLink);
        Assert.Equal(before.Count, store.Users.Count);
        Assert.Equal("Marketing Assistant", store.Users[U2].JobTitle);
        Assert.True(store.Users.ContainsKey(U4));
        Assert.False(store.Users.ContainsKey(U6));
    }

    [Fact]
    public async Task InvalidDeltaToken_410_RestartsFullSyncFromLocation()
    {
        using var fake = NewFakeWithRoundA();
        AddRoundB(fake);
        fake.QueueFault(FakeGraphDirectory.Delta2, new FakeGraphDirectory.Fault(410,
            Location: fake.GraphBase + "/users/delta?" + FakeGraphDirectory.ResyncQuery,
            ErrorCode: "syncStateNotFound", Message: "Delta token is expired."));
        fake.AddPage(FakeGraphDirectory.ResyncQuery, new[]
        {
            FakeGraphDirectory.User(U1, "Adele Vance", "AdeleV@contoso.example", "Retail Manager"),
            FakeGraphDirectory.User(U2, "Alex Wilber", "AlexW@contoso.example", "Marketing Manager"),
        }, nextQuery: FakeGraphDirectory.SkipR);
        fake.AddPage(FakeGraphDirectory.SkipR, new[]
        {
            FakeGraphDirectory.User(U6, "Grady Archie", "GradyA@contoso.example", "Designer"),
        }, deltaQuery: FakeGraphDirectory.Delta3);

        var (store, engine, _) = NewEngine(fake);
        await engine.RunAsync();
        await engine.RunAsync();
        Assert.Equal(4, store.Users.Count);

        SyncResult third = await engine.RunAsync();

        Assert.True(third.FullResync);
        Assert.Equal(1, fake.CountRequests(FakeGraphDirectory.ResyncQuery));
        Assert.Equal(1, fake.CountRequests(FakeGraphDirectory.SkipR));

        // Full resync rebuilds the store: u5 vanished while the token was
        // invalid and must be gone from the local state.
        Assert.Equal(3, store.Users.Count);
        Assert.True(store.Users.ContainsKey(U1));
        Assert.Equal("Marketing Manager", store.Users[U2].JobTitle);
        Assert.True(store.Users.ContainsKey(U6));
        Assert.False(store.Users.ContainsKey(U5));
        Assert.Equal(fake.GraphBase + "/users/delta?" + FakeGraphDirectory.Delta3, store.DeltaLink);
    }

    [Fact]
    public async Task ApiError_SurfacesGraphEnvelope_WithoutRetry_OrTokenLeak()
    {
        using var fake = new FakeGraphDirectory();
        fake.AlwaysFault(FakeGraphDirectory.InitQuery, new FakeGraphDirectory.Fault(403,
            ErrorCode: "Authorization_RequestDenied",
            Message: "Insufficient privileges to complete the operation."));
        var (store, engine, delays) = NewEngine(fake);

        var ex = await Assert.ThrowsAsync<GraphApiException>(() => engine.RunAsync());

        Assert.Equal(403, ex.StatusCode);
        Assert.Equal("Authorization_RequestDenied", ex.ErrorCode);
        Assert.Contains("Insufficient privileges", ex.Message);
        Assert.DoesNotContain(FakeGraphDirectory.Token, ex.Message);
        Assert.Empty(delays);
        Assert.Equal(1, fake.CountRequests(FakeGraphDirectory.InitQuery));
        Assert.Empty(store.Users);
        Assert.Null(store.DeltaLink);
    }

    [Fact]
    public async Task EmptyChangeRound_AdvancesDeltaLinkOnly()
    {
        using var fake = NewFakeWithRoundA();
        fake.AddPage(FakeGraphDirectory.Delta1, Array.Empty<Dictionary<string, object?>>(),
            deltaQuery: FakeGraphDirectory.Delta2);
        var (store, engine, _) = NewEngine(fake);

        await engine.RunAsync();
        SyncResult second = await engine.RunAsync();

        Assert.Equal(0, second.UsersUpserted);
        Assert.Equal(0, second.UsersRemoved);
        Assert.Equal(1, second.Pages);
        Assert.Equal(5, store.Users.Count);
        Assert.Equal(fake.GraphBase + "/users/delta?" + FakeGraphDirectory.Delta2, store.DeltaLink);
    }

    [Fact]
    public async Task ReplayedEntityInOneRound_LastOccurrenceWins()
    {
        using var fake = new FakeGraphDirectory();
        fake.AddPage(FakeGraphDirectory.InitQuery, new[]
        {
            FakeGraphDirectory.User(U1, "Adele Vance", "AdeleV@contoso.example", "Retail Manager"),
            FakeGraphDirectory.User(U2, "Alex Wilber", "AlexW@contoso.example", "Draft Title"),
        }, nextQuery: FakeGraphDirectory.Skip1);
        fake.AddPage(FakeGraphDirectory.Skip1, new[]
        {
            FakeGraphDirectory.User(U2, "Alex Wilber", "AlexW@contoso.example", "Final Title"),
        }, deltaQuery: FakeGraphDirectory.Delta1);
        var (store, engine, _) = NewEngine(fake);

        SyncResult result = await engine.RunAsync();

        Assert.Equal(2, result.UsersUpserted);
        Assert.Equal(2, store.Users.Count);
        Assert.Equal("Final Title", store.Users[U2].JobTitle);
    }
}
