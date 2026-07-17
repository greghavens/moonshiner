// Acceptance tests for the SAP OData V2 change-set writer.
//
// A loopback fake S/4HANA gateway serves the wire contract pinned in
// docs/contract.json: CSRF pre-flight with token + session cookies,
// multipart/mixed $batch framing with Content-IDs, per-operation response
// decoding, token-rejection refresh, and atomic change-set failure.
// No real system, no credentials, no sleeps. Protected — do not modify this
// file, the csproj, .gitignore, or anything under docs/.

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using SapBatch;

namespace SapBatchTests;

public sealed class MockGateway : IDisposable
{
    public sealed record Recorded(string Method, string RawUrl, Dictionary<string, string> Headers, string Body);

    public sealed record Scripted(int Status, string? Body = null, string? ContentType = null,
        Dictionary<string, string>? Headers = null, List<string>? SetCookies = null);

    public List<Recorded> Requests { get; } = new();
    public string Origin { get; }

    private readonly Func<int, Recorded, Scripted> _serve;
    private readonly HttpListener _listener;

    public MockGateway(Func<int, Recorded, Scripted> serve)
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

    public string ServiceRoot => Origin + BatchTests.ServicePath;

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
            catch (Exception e) { s = new Scripted(599, e.ToString(), "text/plain"); }

            ctx.Response.StatusCode = s.Status;
            foreach (var (k, v) in s.Headers ?? new Dictionary<string, string>())
                ctx.Response.Headers[k] = v;
            foreach (var cookie in s.SetCookies ?? new List<string>())
                ctx.Response.Headers.Add("Set-Cookie", cookie);
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

public class BatchTests
{
    public const string ServicePath = "/sap/opu/odata/sap/API_BUSINESS_PARTNER";

    private const string Token1 = "hTgKoDummyCsrf0001==";
    private const string Token2 = "hTgKoDummyCsrf0002==";
    private const string Cookie1 = "SAP_SESSIONID_ER9_100=s3ss10n-c00k13-1; path=/";
    private const string Cookie2 = "sap-usercontext=sap-client=100; path=/";

    private static readonly string CreateBody =
        "{\"BusinessPartnerCategory\":\"2\",\"BusinessPartnerFullName\":\"Vega Dairy Co-op\"}";
    private static readonly string MergeBody =
        "{\"BusinessPartnerFullName\":\"Nordsee Logistik SE\"}";

    private static bool IsCsrfFetch(MockGateway.Recorded rec) =>
        (rec.Method == "HEAD" || rec.Method == "GET")
        && rec.Headers.TryGetValue("x-csrf-token", out var v)
        && v.Equals("fetch", StringComparison.OrdinalIgnoreCase);

    private static MockGateway.Scripted CsrfReply(string token) => new(
        200, "", "text/plain",
        new Dictionary<string, string> { ["x-csrf-token"] = token },
        new List<string> { Cookie1, Cookie2 });

    private static BatchRequestBuilder SampleBatch() {
        var batch = new BatchRequestBuilder("batch_fx01", "changeset_fx01");
        batch.AddRetrieve("A_BusinessPartner('1000234')?$format=json");
        batch.AddChangeset(
            BatchWriteOperation.Post("A_BusinessPartner", CreateBody, 1),
            BatchWriteOperation.Merge("A_BusinessPartner('1000388')", MergeBody, 2));
        return batch;
    }

    private static string SuccessResponseBody() =>
        "--batchresponse_77\r\n" +
        "Content-Type: application/http\r\n" +
        "Content-Transfer-Encoding: binary\r\n" +
        "\r\n" +
        "HTTP/1.1 200 OK\r\n" +
        "Content-Type: application/json\r\n" +
        "\r\n" +
        "{\"d\":{\"BusinessPartner\":\"1000234\",\"BusinessPartnerFullName\":\"Miller Farms GmbH\"}}\r\n" +
        "--batchresponse_77\r\n" +
        "Content-Type: multipart/mixed; boundary=changesetresponse_78\r\n" +
        "\r\n" +
        "--changesetresponse_78\r\n" +
        "Content-Type: application/http\r\n" +
        "Content-Transfer-Encoding: binary\r\n" +
        "Content-ID: 1\r\n" +
        "\r\n" +
        "HTTP/1.1 201 Created\r\n" +
        "Content-Type: application/json\r\n" +
        "Location: " + ServicePath + "/A_BusinessPartner('1000900')\r\n" +
        "\r\n" +
        "{\"d\":{\"BusinessPartner\":\"1000900\",\"BusinessPartnerFullName\":\"Vega Dairy Co-op\"}}\r\n" +
        "--changesetresponse_78\r\n" +
        "Content-Type: application/http\r\n" +
        "Content-Transfer-Encoding: binary\r\n" +
        "Content-ID: 2\r\n" +
        "\r\n" +
        "HTTP/1.1 204 No Content\r\n" +
        "\r\n" +
        "\r\n" +
        "--changesetresponse_78--\r\n" +
        "--batchresponse_77--\r\n";

    private static string FailedChangesetResponseBody() =>
        "--batchresponse_90\r\n" +
        "Content-Type: application/http\r\n" +
        "Content-Transfer-Encoding: binary\r\n" +
        "\r\n" +
        "HTTP/1.1 200 OK\r\n" +
        "Content-Type: application/json\r\n" +
        "\r\n" +
        "{\"d\":{\"BusinessPartner\":\"1000234\"}}\r\n" +
        "--batchresponse_90\r\n" +
        "Content-Type: multipart/mixed; boundary=changesetresponse_91\r\n" +
        "\r\n" +
        "--changesetresponse_91\r\n" +
        "Content-Type: application/http\r\n" +
        "Content-Transfer-Encoding: binary\r\n" +
        "\r\n" +
        "HTTP/1.1 400 Bad Request\r\n" +
        "Content-Type: application/json\r\n" +
        "\r\n" +
        "{\"error\":{\"code\":\"/IWBEP/CX_MGW_BUSI_EXCEPTION\"," +
        "\"message\":{\"lang\":\"en\",\"value\":\"Business partner 1000388 is locked by user MAINTAIN01\"}," +
        "\"innererror\":{\"application\":{\"component_id\":\"LO-MD-BP\"},\"transactionid\":\"D91A2F7B33AA00F1\"}}}\r\n" +
        "--changesetresponse_91--\r\n" +
        "--batchresponse_90--\r\n";

    // -------------------------------------------------------------- framing

    [Fact]
    public void BatchPayloadUsesExactMultipartFraming()
    {
        string payload = SampleBatch().Build();

        string expected =
            "--batch_fx01\r\n" +
            "Content-Type: application/http\r\n" +
            "Content-Transfer-Encoding: binary\r\n" +
            "\r\n" +
            "GET A_BusinessPartner('1000234')?$format=json HTTP/1.1\r\n" +
            "Accept: application/json\r\n" +
            "\r\n" +
            "\r\n" +
            "--batch_fx01\r\n" +
            "Content-Type: multipart/mixed; boundary=changeset_fx01\r\n" +
            "\r\n" +
            "--changeset_fx01\r\n" +
            "Content-Type: application/http\r\n" +
            "Content-Transfer-Encoding: binary\r\n" +
            "Content-ID: 1\r\n" +
            "\r\n" +
            "POST A_BusinessPartner HTTP/1.1\r\n" +
            "Content-Type: application/json\r\n" +
            "\r\n" +
            CreateBody + "\r\n" +
            "--changeset_fx01\r\n" +
            "Content-Type: application/http\r\n" +
            "Content-Transfer-Encoding: binary\r\n" +
            "Content-ID: 2\r\n" +
            "\r\n" +
            "MERGE A_BusinessPartner('1000388') HTTP/1.1\r\n" +
            "Content-Type: application/json\r\n" +
            "\r\n" +
            MergeBody + "\r\n" +
            "--changeset_fx01--\r\n" +
            "--batch_fx01--\r\n";

        Assert.Equal(expected, payload);
        Assert.DoesNotContain("\n", payload.Replace("\r\n", ""));
    }

    // -------------------------------------------------- csrf fetch + submit

    [Fact]
    public async Task SubmitFetchesTokenOnceAndDecodesPerOperationResults()
    {
        using var gw = new MockGateway((n, rec) =>
        {
            if (IsCsrfFetch(rec))
            {
                Assert.Equal(ServicePath + "/", rec.RawUrl);
                return CsrfReply(Token1);
            }
            Assert.Equal("POST", rec.Method);
            Assert.Equal(ServicePath + "/$batch", rec.RawUrl);
            Assert.Equal(Token1, rec.Headers["x-csrf-token"]);
            Assert.StartsWith("multipart/mixed; boundary=batch_fx01", rec.Headers["content-type"]);
            Assert.Contains("SAP_SESSIONID_ER9_100=s3ss10n-c00k13-1", rec.Headers["cookie"]);
            Assert.Contains("sap-usercontext=sap-client=100", rec.Headers["cookie"]);
            Assert.Contains("--changeset_fx01--", rec.Body);
            return new MockGateway.Scripted(202, SuccessResponseBody(),
                "multipart/mixed; boundary=batchresponse_77");
        });

        var gateway = new BatchGateway(new HttpClient(), gw.ServiceRoot);
        BatchResponse first = await gateway.SubmitAsync(SampleBatch());
        BatchResponse second = await gateway.SubmitAsync(SampleBatch());

        var requests = gw.Snapshot();
        Assert.Equal(3, requests.Length);
        Assert.True(IsCsrfFetch(requests[0]), "first request is the CSRF pre-flight");
        Assert.Equal(1, requests.Count(IsCsrfFetch));

        Assert.Single(first.Retrieves);
        Assert.Equal(200, first.Retrieves[0].Status);
        using var doc = JsonDocument.Parse(first.Retrieves[0].Body!);
        Assert.Equal("Miller Farms GmbH",
            doc.RootElement.GetProperty("d").GetProperty("BusinessPartnerFullName").GetString());

        Assert.Single(first.Changesets);
        ChangesetResult cs = first.Changesets[0];
        Assert.True(cs.Succeeded);
        Assert.Null(cs.Error);
        Assert.Equal(2, cs.Operations.Count);
        Assert.Equal(201, cs.Operations[1].Status);
        Assert.Equal(ServicePath + "/A_BusinessPartner('1000900')", cs.Operations[1].Location);
        using var created = JsonDocument.Parse(cs.Operations[1].Body!);
        Assert.Equal("1000900",
            created.RootElement.GetProperty("d").GetProperty("BusinessPartner").GetString());
        Assert.Equal(204, cs.Operations[2].Status);
        Assert.Null(cs.Operations[2].Body);

        Assert.Single(second.Retrieves);
        Assert.Single(second.Changesets);
    }

    // ------------------------------------------------- token rejection path

    [Fact]
    public async Task RejectedTokenIsRefreshedOnceAndBatchRetried()
    {
        int fetches = 0;
        using var gw = new MockGateway((n, rec) =>
        {
            if (IsCsrfFetch(rec))
            {
                fetches++;
                return CsrfReply(fetches == 1 ? Token1 : Token2);
            }
            if (rec.Headers["x-csrf-token"] == Token1)
                return new MockGateway.Scripted(403, "CSRF token validation failed", "text/plain",
                    new Dictionary<string, string> { ["x-csrf-token"] = "Required" });
            Assert.Equal(Token2, rec.Headers["x-csrf-token"]);
            return new MockGateway.Scripted(202, SuccessResponseBody(),
                "multipart/mixed; boundary=batchresponse_77");
        });

        var gateway = new BatchGateway(new HttpClient(), gw.ServiceRoot);
        BatchResponse response = await gateway.SubmitAsync(SampleBatch());

        var requests = gw.Snapshot();
        Assert.Equal(new[] { true, false, true, false },
            requests.Select(IsCsrfFetch).ToArray());
        Assert.Equal(requests[1].Body, requests[3].Body);
        Assert.True(response.Changesets[0].Succeeded);
    }

    [Fact]
    public async Task SecondConsecutiveRejectionSurfacesWithoutTokenLeak()
    {
        using var gw = new MockGateway((n, rec) =>
        {
            if (IsCsrfFetch(rec)) return CsrfReply(Token1);
            return new MockGateway.Scripted(403, "CSRF token validation failed", "text/plain",
                new Dictionary<string, string> { ["x-csrf-token"] = "Required" });
        });

        var gateway = new BatchGateway(new HttpClient(), gw.ServiceRoot);
        var ex = await Assert.ThrowsAsync<SapCsrfException>(() => gateway.SubmitAsync(SampleBatch()));
        Assert.DoesNotContain(Token1, ex.Message);
        Assert.Equal(4, gw.Snapshot().Length); // fetch, post, re-fetch, post — then stop
    }

    // ------------------------------------------------ atomic changeset fail

    [Fact]
    public async Task FailedChangesetIsAtomicAndPreservesSapError()
    {
        using var gw = new MockGateway((n, rec) =>
        {
            if (IsCsrfFetch(rec)) return CsrfReply(Token1);
            return new MockGateway.Scripted(202, FailedChangesetResponseBody(),
                "multipart/mixed; boundary=batchresponse_90");
        });

        var gateway = new BatchGateway(new HttpClient(), gw.ServiceRoot);
        BatchResponse response = await gateway.SubmitAsync(SampleBatch());

        Assert.Single(response.Retrieves);
        Assert.Equal(200, response.Retrieves[0].Status);

        ChangesetResult cs = response.Changesets[0];
        Assert.False(cs.Succeeded);
        Assert.Empty(cs.Operations); // all-or-nothing: no operation applied
        Assert.NotNull(cs.Error);
        Assert.Equal("/IWBEP/CX_MGW_BUSI_EXCEPTION", cs.Error!.Code);
        Assert.Contains("locked by user MAINTAIN01", cs.Error.Message);
        using var raw = JsonDocument.Parse(cs.Error.RawJson);
        Assert.Equal("D91A2F7B33AA00F1",
            raw.RootElement.GetProperty("innererror").GetProperty("transactionid").GetString());
        Assert.Equal("LO-MD-BP",
            raw.RootElement.GetProperty("innererror").GetProperty("application")
                .GetProperty("component_id").GetString());
    }

    // ----------------------------------------------------- builder hygiene

    [Fact]
    public void ContentIdsMustBeUniqueWithinAChangeset()
    {
        var batch = new BatchRequestBuilder("batch_fx02", "changeset_fx02");
        var ex = Assert.Throws<ArgumentException>(() => batch.AddChangeset(
            BatchWriteOperation.Post("A_BusinessPartner", CreateBody, 1),
            BatchWriteOperation.Merge("A_BusinessPartner('1')", MergeBody, 1)));
        Assert.Contains("Content-ID", ex.Message);
    }

    [Fact]
    public void RetrieveAfterChangesetKeepsDocumentedPartOrder()
    {
        var batch = new BatchRequestBuilder("batch_fx03", "changeset_fx03");
        batch.AddChangeset(BatchWriteOperation.Post("A_BusinessPartner", CreateBody, 1));
        batch.AddRetrieve("A_BusinessPartner?$format=json&$top=1");
        string payload = batch.Build();
        int changesetAt = payload.IndexOf("--changeset_fx03\r\n", StringComparison.Ordinal);
        int retrieveAt = payload.IndexOf("GET A_BusinessPartner?$format=json&$top=1 HTTP/1.1", StringComparison.Ordinal);
        Assert.True(changesetAt >= 0 && retrieveAt > changesetAt,
            "parts are serialized in the order they were added");
        Assert.EndsWith("--batch_fx03--\r\n", payload);
    }
}
