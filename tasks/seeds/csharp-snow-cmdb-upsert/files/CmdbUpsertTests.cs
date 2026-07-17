// Acceptance tests for the CMDB identification/reconciliation upserter.
//
// Runs a loopback fake ServiceNow instance implementing the
// Identification and Reconciliation API subset pinned in docs/contract.json.
// No vendor network, no real credentials. The fake rejects (and counts) any
// attempt to write through the Table API instead.

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using SnowCmdb;

namespace CmdbUpsertTests;

public sealed class FakeSnowInstance : IDisposable
{
    public const string Username = "cmdb.bot";
    public const string Password = "dummy-cred-5aa3e9"; // dummy; must never leak
    public static readonly string ExpectedAuth =
        "Basic " + Convert.ToBase64String(Encoding.UTF8.GetBytes($"{Username}:{Password}"));

    public sealed record Recorded(
        string Method, string Path, Dictionary<string, string> Query,
        Dictionary<string, string> Headers, string Body);

    public sealed record Fault(int Status, int RetryAfter);

    public List<Recorded> Requests { get; } = new();
    public Queue<Fault> Faults { get; } = new();
    public Fault? AlwaysFault { get; set; }
    public int TableApiHits { get; private set; }
    public string BaseUrl { get; }

    private readonly Dictionary<string, (string SysId, string Canon)> _store = new();
    private readonly HttpListener _listener;
    private readonly Task _loop;
    private int _counter;

    public FakeSnowInstance()
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

    public int PostCount => Requests.Count(r => r.Method == "POST");

    private async Task LoopAsync()
    {
        while (_listener.IsListening)
        {
            HttpListenerContext ctx;
            try { ctx = await _listener.GetContextAsync(); }
            catch (Exception) { return; }
            try { await HandleAsync(ctx); }
            catch (Exception ex)
            {
                TryRespond(ctx, 500, Envelope("Unexpected mock failure", ex.Message));
            }
        }
    }

    private static string Envelope(string message, string detail) =>
        JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["error"] = new Dictionary<string, object?> { ["message"] = message, ["detail"] = detail },
            ["status"] = "failure",
        });

    private static void TryRespond(HttpListenerContext ctx, int status, string body,
        Dictionary<string, string>? extraHeaders = null)
    {
        try
        {
            byte[] bytes = Encoding.UTF8.GetBytes(body);
            ctx.Response.StatusCode = status;
            ctx.Response.ContentType = "application/json";
            if (extraHeaders is not null)
                foreach (var (k, v) in extraHeaders) ctx.Response.AddHeader(k, v);
            ctx.Response.ContentLength64 = bytes.Length;
            ctx.Response.OutputStream.Write(bytes);
            ctx.Response.Close();
        }
        catch (Exception) { /* client went away */ }
    }

    private async Task HandleAsync(HttpListenerContext ctx)
    {
        var req = ctx.Request;
        string body;
        using (var reader = new StreamReader(req.InputStream, Encoding.UTF8))
            body = await reader.ReadToEndAsync();

        var query = new Dictionary<string, string>();
        foreach (string? key in req.QueryString.AllKeys)
            if (key is not null) query[key] = req.QueryString[key] ?? "";
        var headers = new Dictionary<string, string>();
        foreach (string? key in req.Headers.AllKeys)
            if (key is not null) headers[key.ToLowerInvariant()] = req.Headers[key] ?? "";
        Requests.Add(new Recorded(req.HttpMethod, req.Url!.AbsolutePath, query, headers, body));

        if (headers.GetValueOrDefault("authorization") != ExpectedAuth)
        {
            TryRespond(ctx, 401, Envelope("User Not Authenticated",
                "Required to provide Auth information"));
            return;
        }
        if (req.Url!.AbsolutePath.StartsWith("/api/now/table", StringComparison.Ordinal))
        {
            TableApiHits++;
            TryRespond(ctx, 400, Envelope("Use the Identification and Reconciliation API",
                "Direct Table API writes bypass CMDB identification and reconciliation rules"));
            return;
        }
        if (req.HttpMethod != "POST" || req.Url!.AbsolutePath != "/api/now/identifyreconcile")
        {
            TryRespond(ctx, 400, Envelope("Unsupported operation",
                $"{req.HttpMethod} {req.Url!.AbsolutePath}"));
            return;
        }

        var fault = AlwaysFault ?? (Faults.Count > 0 ? Faults.Dequeue() : null);
        if (fault is not null)
        {
            var extra = new Dictionary<string, string>();
            if (fault.RetryAfter > 0) extra["Retry-After"] = fault.RetryAfter.ToString();
            TryRespond(ctx, fault.Status,
                Envelope($"Fault {fault.Status}", $"injected fault {fault.Status}"), extra);
            return;
        }

        if (headers.GetValueOrDefault("content-type", "").Split(';')[0].Trim()
                != "application/json"
            || headers.GetValueOrDefault("accept") != "application/json")
        {
            TryRespond(ctx, 400, Envelope("Invalid headers",
                "Accept and Content-Type must be application/json"));
            return;
        }
        string dataSource = query.GetValueOrDefault("sysparm_data_source", "");
        if (dataSource.Length == 0)
        {
            TryRespond(ctx, 400, Envelope("Missing parameter", "sysparm_data_source is required"));
            return;
        }

        using var doc = JsonDocument.Parse(body);
        if (!doc.RootElement.TryGetProperty("items", out var items)
            || items.ValueKind != JsonValueKind.Array)
        {
            TryRespond(ctx, 400, Envelope("Invalid payload", "request body must carry an items array"));
            return;
        }

        var results = new List<Dictionary<string, object?>>();
        int index = 0;
        foreach (var item in items.EnumerateArray())
        {
            string? className = item.TryGetProperty("className", out var cn) ? cn.GetString() : null;
            if (string.IsNullOrEmpty(className)
                || !item.TryGetProperty("values", out var values)
                || values.ValueKind != JsonValueKind.Object
                || !values.EnumerateObject().Any()
                || !item.TryGetProperty("sys_object_source_info", out var sosi)
                || sosi.GetProperty("source_name").GetString() != dataSource
                || string.IsNullOrEmpty(sosi.GetProperty("source_native_key").GetString()))
            {
                TryRespond(ctx, 400, Envelope("Invalid payload",
                    $"item {index} must carry className, non-empty values, and sys_object_source_info with source_name={dataSource} and source_native_key"));
                return;
            }
            results.Add(Reconcile(className!, values,
                sosi.GetProperty("source_native_key").GetString()!, index));
            index++;
        }
        results.Reverse(); // clients must map results back via inputIndices
        bool hasError = results.Any(r => (int)r["errorCount"]! > 0);
        string response = JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["result"] = new Dictionary<string, object?>
            {
                ["items"] = results,
                ["relations"] = Array.Empty<object>(),
                ["hasError"] = hasError,
                ["hasWarning"] = false,
                ["logContextId"] = "log0000000000000000000000000ctx1",
            },
        });
        TryRespond(ctx, 200, response);
    }

    private static string Canon(JsonElement values) =>
        string.Join("|", values.EnumerateObject()
            .OrderBy(p => p.Name, StringComparer.Ordinal)
            .Select(p => p.Name + "=" + p.Value.GetRawText()));

    private Dictionary<string, object?> Reconcile(
        string className, JsonElement values, string nativeKey, int index)
    {
        if (className == "cmdb_ci_unknown_widget")
        {
            return new Dictionary<string, object?>
            {
                ["className"] = className,
                ["operation"] = null,
                ["sysId"] = null,
                ["identifierEntrySysId"] = null,
                ["errorCount"] = 1,
                ["errors"] = new object[]
                {
                    new Dictionary<string, object?>
                    {
                        ["error"] = "IDENTIFICATION_RULE_MISSING",
                        ["message"] = $"No identification rule found for class {className}",
                    },
                },
                ["warnings"] = Array.Empty<object>(),
                ["identificationAttempts"] = Array.Empty<object>(),
                ["inputIndices"] = new[] { index },
            };
        }
        string canon = Canon(values);
        string op;
        string sysId;
        if (!_store.TryGetValue(nativeKey, out var existing))
        {
            _counter++;
            sysId = _counter.ToString().PadLeft(32, '0');
            _store[nativeKey] = (sysId, canon);
            op = "INSERT";
        }
        else if (existing.Canon != canon)
        {
            sysId = existing.SysId;
            _store[nativeKey] = (sysId, canon);
            op = "UPDATE";
        }
        else
        {
            sysId = existing.SysId;
            op = "NO_CHANGE";
        }
        return new Dictionary<string, object?>
        {
            ["className"] = className,
            ["operation"] = op,
            ["sysId"] = sysId,
            ["identifierEntrySysId"] = "ie".PadRight(32, '0'),
            ["errorCount"] = 0,
            ["errors"] = Array.Empty<object>(),
            ["warnings"] = Array.Empty<object>(),
            ["identificationAttempts"] = new object[]
            {
                new Dictionary<string, object?>
                {
                    ["identifierName"] = "Hardware Rule",
                    ["attemptResult"] = op == "INSERT" ? "NO_MATCH" : "MATCHED",
                    ["attributes"] = new[] { "serial_number" },
                    ["searchOnTable"] = className,
                },
            },
            ["inputIndices"] = new[] { index },
        };
    }

    public void Dispose()
    {
        try { _listener.Stop(); } catch (Exception) { }
        try { _loop.Wait(TimeSpan.FromSeconds(2)); } catch (Exception) { }
    }
}

public class CmdbUpsertClientTests
{
    private const string DataSource = "asset_scanner";

    private static (FakeSnowInstance Inst, CmdbUpsertClient Client, List<TimeSpan> Delays) Fresh()
    {
        var inst = new FakeSnowInstance();
        var delays = new List<TimeSpan>();
        var client = new CmdbUpsertClient(
            inst.BaseUrl, FakeSnowInstance.Username, FakeSnowInstance.Password,
            delay: ts => { delays.Add(ts); return Task.CompletedTask; },
            maxRetries: 3);
        return (inst, client, delays);
    }

    private static CmdbItem Server(string key, string name, string serial, string os) => new()
    {
        ClassName = "cmdb_ci_linux_server",
        SourceNativeKey = key,
        Values = new Dictionary<string, object?>
        {
            ["name"] = name,
            ["serial_number"] = serial,
            ["os_version"] = os,
            ["ram"] = 65536,
        },
    };

    [Fact]
    public async Task Upsert_posts_the_documented_identifyreconcile_payload()
    {
        using var inst = DisposableTuple(Fresh(), out var c, out var delays);
        var report = await c.UpsertAsync(DataSource, new[]
        {
            Server("scan:web-01", "web-01", "SN-1001", "Ubuntu 24.04"),
            Server("scan:web-02", "web-02", "SN-1002", "Ubuntu 24.04"),
        });

        Assert.Equal(2, report.Items.Count);
        Assert.All(report.Items, r => Assert.Equal("INSERT", r.Operation));
        Assert.All(report.Items, r => Assert.Equal(32, r.SysId!.Length));
        Assert.NotEqual(report.Items[0].SysId, report.Items[1].SysId);
        Assert.False(report.HasErrors);
        Assert.Equal(2, report.Inserted);
        Assert.Equal(0, report.Updated);
        Assert.Equal(0, report.Unchanged);
        Assert.Equal(0, report.Failed);

        var post = Assert.Single(inst.Requests, r => r.Method == "POST");
        Assert.Equal("/api/now/identifyreconcile", post.Path);
        Assert.Equal(DataSource, post.Query["sysparm_data_source"]);
        Assert.Equal("application/json", post.Headers["accept"]);
        Assert.StartsWith("application/json", post.Headers["content-type"]);
        Assert.Equal(FakeSnowInstance.ExpectedAuth, post.Headers["authorization"]);

        using var doc = JsonDocument.Parse(post.Body);
        var items = doc.RootElement.GetProperty("items");
        Assert.Equal(2, items.GetArrayLength());
        var first = items[0];
        Assert.Equal("cmdb_ci_linux_server", first.GetProperty("className").GetString());
        var values = first.GetProperty("values");
        Assert.Equal("web-01", values.GetProperty("name").GetString());
        Assert.Equal("SN-1001", values.GetProperty("serial_number").GetString());
        Assert.Equal("Ubuntu 24.04", values.GetProperty("os_version").GetString());
        Assert.Equal(65536, values.GetProperty("ram").GetInt32());
        var sosi = first.GetProperty("sys_object_source_info");
        Assert.Equal(DataSource, sosi.GetProperty("source_name").GetString());
        Assert.Equal("scan:web-01", sosi.GetProperty("source_native_key").GetString());

        Assert.Equal(0, inst.TableApiHits); // never a naive Table API write
    }

    [Fact]
    public async Task Results_map_back_to_inputs_via_inputIndices()
    {
        using var inst = DisposableTuple(Fresh(), out var c, out _);
        var items = new[]
        {
            new CmdbItem
            {
                ClassName = "cmdb_ci_linux_server",
                SourceNativeKey = "scan:app-01",
                Values = new Dictionary<string, object?> { ["name"] = "app-01" },
            },
            new CmdbItem
            {
                ClassName = "cmdb_ci_db_mysql_instance",
                SourceNativeKey = "scan:db-01",
                Values = new Dictionary<string, object?> { ["name"] = "db-01" },
            },
            new CmdbItem
            {
                ClassName = "cmdb_ci_lb_haproxy",
                SourceNativeKey = "scan:lb-01",
                Values = new Dictionary<string, object?> { ["name"] = "lb-01" },
            },
        };
        var report = await c.UpsertAsync(DataSource, items);
        Assert.Equal(3, report.Items.Count);
        // the fake returns response items in REVERSED order with correct
        // inputIndices; per-position class names only line up when mapped back
        for (int i = 0; i < items.Length; i++)
        {
            Assert.Equal(items[i].ClassName, report.Items[i].ClassName);
            Assert.Equal("INSERT", report.Items[i].Operation);
            Assert.NotNull(report.Items[i].SysId);
        }
    }

    [Fact]
    public async Task Reupserts_keep_sysId_identity_and_detect_changes()
    {
        using var inst = DisposableTuple(Fresh(), out var c, out _);
        var first = await c.UpsertAsync(DataSource, new[]
        {
            Server("scan:web-01", "web-01", "SN-1001", "Ubuntu 24.04"),
        });
        string sysId = first.Items[0].SysId!;
        Assert.Equal("INSERT", first.Items[0].Operation);

        var same = await c.UpsertAsync(DataSource, new[]
        {
            Server("scan:web-01", "web-01", "SN-1001", "Ubuntu 24.04"),
        });
        Assert.Equal("NO_CHANGE", same.Items[0].Operation);
        Assert.Equal(sysId, same.Items[0].SysId);
        Assert.Equal(1, same.Unchanged);
        Assert.Equal(0, same.Inserted);

        var changed = await c.UpsertAsync(DataSource, new[]
        {
            Server("scan:web-01", "web-01", "SN-1001", "Ubuntu 26.04"),
        });
        Assert.Equal("UPDATE", changed.Items[0].Operation);
        Assert.Equal(sysId, changed.Items[0].SysId);
        Assert.Equal(1, changed.Updated);
        Assert.Equal(0, changed.Failed);
    }

    [Fact]
    public async Task Partial_success_reports_failed_items_without_hiding_siblings()
    {
        using var inst = DisposableTuple(Fresh(), out var c, out _);
        await c.UpsertAsync(DataSource, new[]
        {
            Server("scan:web-09", "web-09", "SN-9009", "Ubuntu 24.04"),
        });

        var report = await c.UpsertAsync(DataSource, new[]
        {
            Server("scan:new-01", "new-01", "SN-7001", "Ubuntu 24.04"),
            new CmdbItem
            {
                ClassName = "cmdb_ci_unknown_widget",
                SourceNativeKey = "scan:widget-01",
                Values = new Dictionary<string, object?> { ["name"] = "widget-01" },
            },
            Server("scan:web-09", "web-09", "SN-9009", "Ubuntu 24.04"),
        });

        Assert.True(report.HasErrors, "a failed item must set HasErrors");
        Assert.Equal(1, report.Inserted);
        Assert.Equal(1, report.Unchanged);
        Assert.Equal(1, report.Failed);
        Assert.Equal(0, report.Updated);

        var failed = report.Items[1];
        Assert.Null(failed.SysId);
        Assert.Null(failed.Operation);
        var error = Assert.Single(failed.Errors);
        Assert.Equal("IDENTIFICATION_RULE_MISSING", error.Error);
        Assert.Contains("cmdb_ci_unknown_widget", error.Message);

        Assert.Equal("INSERT", report.Items[0].Operation);
        Assert.NotNull(report.Items[0].SysId);
        Assert.Equal("NO_CHANGE", report.Items[2].Operation);
        Assert.NotNull(report.Items[2].SysId);
    }

    [Fact]
    public async Task Rate_limit_waits_RetryAfter_then_retries()
    {
        using var inst = DisposableTuple(Fresh(), out var c, out var delays);
        inst.Faults.Enqueue(new FakeSnowInstance.Fault(429, 6));
        var report = await c.UpsertAsync(DataSource, new[]
        {
            Server("scan:web-01", "web-01", "SN-1001", "Ubuntu 24.04"),
        });
        Assert.Equal(1, report.Inserted);
        Assert.Equal(new[] { TimeSpan.FromSeconds(6) }, delays);
        Assert.Equal(2, inst.PostCount);
    }

    [Fact]
    public async Task Persistent_rate_limit_exhausts_retries()
    {
        using var inst = DisposableTuple(Fresh(), out var c, out var delays);
        inst.AlwaysFault = new FakeSnowInstance.Fault(429, 2);
        var ex = await Assert.ThrowsAsync<SnowRateLimitError>(() =>
            c.UpsertAsync(DataSource, new[]
            {
                Server("scan:web-01", "web-01", "SN-1001", "Ubuntu 24.04"),
            }));
        Assert.IsAssignableFrom<SnowApiError>(ex);
        Assert.Equal(429, ex.StatusCode);
        Assert.Equal(2, ex.RetryAfterSeconds);
        Assert.Equal(3, delays.Count);
        Assert.Equal(4, inst.PostCount); // original attempt + 3 retries
        Assert.DoesNotContain(FakeSnowInstance.Password, ex.ToString());
    }

    [Fact]
    public async Task Error_envelope_surfaces_status_message_detail()
    {
        using var inst = DisposableTuple(Fresh(), out var c, out _);
        inst.Faults.Enqueue(new FakeSnowInstance.Fault(403, 0));
        var ex = await Assert.ThrowsAsync<SnowApiError>(() =>
            c.UpsertAsync(DataSource, new[]
            {
                Server("scan:web-01", "web-01", "SN-1001", "Ubuntu 24.04"),
            }));
        Assert.Equal(403, ex.StatusCode);
        Assert.Equal("Fault 403", ex.Message);
        Assert.Equal("injected fault 403", ex.Detail);
        Assert.Equal(1, inst.PostCount); // non-429 errors are not retried
        Assert.DoesNotContain(FakeSnowInstance.Password, ex.ToString());
    }

    [Fact]
    public void Protected_docs_fixtures_are_intact_and_first_party()
    {
        string root = AppContext.BaseDirectory;
        using var contract = JsonDocument.Parse(
            File.ReadAllText(Path.Combine(root, "docs", "contract.json")));
        using var sources = JsonDocument.Parse(
            File.ReadAllText(Path.Combine(root, "docs", "official_sources.json")));

        var research = sources.RootElement.GetProperty("research");
        Assert.True(research.GetProperty("required").GetBoolean());
        var official = research.GetProperty("official_sources");
        Assert.True(official.GetArrayLength() >= 2, "at least two official sources required");
        foreach (var src in official.EnumerateArray())
        {
            Assert.StartsWith("https://", src.GetProperty("url").GetString());
            Assert.Contains("servicenow.com", src.GetProperty("url").GetString());
            Assert.False(string.IsNullOrEmpty(src.GetProperty("used_for").GetString()));
        }
        Assert.True(sources.RootElement.GetProperty("verified_facts").GetArrayLength() >= 4);

        var endpoint = contract.RootElement.GetProperty("endpoint");
        Assert.Equal("POST", endpoint.GetProperty("method").GetString());
        Assert.Equal("/api/now/identifyreconcile", endpoint.GetProperty("path").GetString());
        Assert.Equal(3, contract.RootElement.GetProperty("rate_limit")
            .GetProperty("max_retries").GetInt32());
        Assert.Equal("Retry-After", contract.RootElement.GetProperty("rate_limit")
            .GetProperty("retry_after_header").GetString());
    }

    // Helper so each test can `using` the fake instance while unpacking the tuple.
    private static FakeSnowInstance DisposableTuple(
        (FakeSnowInstance Inst, CmdbUpsertClient Client, List<TimeSpan> Delays) t,
        out CmdbUpsertClient client, out List<TimeSpan> delays)
    {
        client = t.Client;
        delays = t.Delays;
        return t.Inst;
    }
}
