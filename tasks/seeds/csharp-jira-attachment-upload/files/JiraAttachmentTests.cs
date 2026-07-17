// Acceptance tests for the Jira attachment client.
//
// Runs loopback fake Jira endpoints implementing the REST v3 attachment wire
// contract pinned in docs/contract.json. No vendor network, no real
// credentials, no sleeps.

using System.Net;
using System.Net.Sockets;
using System.Text;
using JiraFiles;

namespace JiraAttachmentTests;

public sealed class MockServer : IDisposable
{
    public sealed record Recorded(
        string Method, string Path, Dictionary<string, string> Headers, byte[] Body);

    public sealed record Scripted(
        int Status, string? Json = null, byte[]? Bytes = null,
        Dictionary<string, string>? Headers = null, string? ContentType = null);

    public List<Recorded> Requests { get; } = new();
    public string BaseUrl { get; }

    private readonly Func<int, Recorded, Scripted> _serve;
    private readonly HttpListener _listener;

    public MockServer(Func<int, Recorded, Scripted> serve)
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

            var req = ctx.Request;
            using var buffer = new MemoryStream();
            await req.InputStream.CopyToAsync(buffer);
            var headers = new Dictionary<string, string>();
            foreach (string? key in req.Headers.AllKeys)
                if (key is not null) headers[key.ToLowerInvariant()] = req.Headers[key] ?? "";
            var recorded = new Recorded(req.HttpMethod, req.Url!.AbsolutePath, headers, buffer.ToArray());
            int n;
            lock (Requests) { n = Requests.Count; Requests.Add(recorded); }

            Scripted scripted;
            try { scripted = _serve(n, recorded); }
            catch (Exception ex) { scripted = new Scripted(500, "{\"errorMessages\":[\"mock: " + ex.Message + "\"],\"errors\":{}}"); }

            try
            {
                ctx.Response.StatusCode = scripted.Status;
                if (scripted.Headers is not null)
                    foreach (var (k, v) in scripted.Headers) ctx.Response.AddHeader(k, v);
                byte[] payload = scripted.Bytes
                    ?? (scripted.Json is null ? Array.Empty<byte>() : Encoding.UTF8.GetBytes(scripted.Json));
                if (payload.Length > 0)
                {
                    ctx.Response.ContentType = scripted.ContentType
                        ?? (scripted.Json is null ? "application/octet-stream" : "application/json;charset=UTF-8");
                    ctx.Response.ContentLength64 = payload.Length;
                    ctx.Response.OutputStream.Write(payload);
                }
                ctx.Response.Close();
            }
            catch (Exception) { /* client went away */ }
        }
    }

    public void Dispose()
    {
        _listener.Stop();
        _listener.Close();
    }
}

public static class Multipart
{
    public sealed record Part(Dictionary<string, string> Headers, byte[] Content);

    public static List<Part> Parse(byte[] body, string contentType)
    {
        const string marker = "boundary=";
        int bi = contentType.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
        Assert.True(bi >= 0, "multipart content type must carry a boundary");
        string boundary = contentType[(bi + marker.Length)..].Trim();
        if (boundary.Contains(';')) boundary = boundary[..boundary.IndexOf(';')];
        boundary = boundary.Trim('"');
        Assert.True(boundary.Length > 0, "empty multipart boundary");

        byte[] open = Encoding.ASCII.GetBytes("--" + boundary);
        byte[] sep = Encoding.ASCII.GetBytes("\r\n\r\n");
        byte[] next = Encoding.ASCII.GetBytes("\r\n--" + boundary);

        var parts = new List<Part>();
        int pos = IndexOf(body, open, 0);
        Assert.True(pos >= 0, "multipart body must start with the boundary delimiter");
        while (true)
        {
            int afterDelim = pos + open.Length;
            if (afterDelim + 1 < body.Length && body[afterDelim] == (byte)'-' && body[afterDelim + 1] == (byte)'-')
                break; // closing delimiter "--boundary--"
            int headerStart = afterDelim + 2; // skip CRLF after the delimiter
            int headersEnd = IndexOf(body, sep, headerStart);
            Assert.True(headersEnd >= 0, "part headers must end with a blank line");
            var headers = new Dictionary<string, string>();
            string headerBlock = Encoding.UTF8.GetString(body, headerStart, headersEnd - headerStart);
            foreach (string line in headerBlock.Split("\r\n"))
            {
                int colon = line.IndexOf(':');
                Assert.True(colon > 0, $"malformed part header line: {line}");
                headers[line[..colon].Trim().ToLowerInvariant()] = line[(colon + 1)..].Trim();
            }
            int contentStart = headersEnd + sep.Length;
            int contentEnd = IndexOf(body, next, contentStart);
            Assert.True(contentEnd >= 0, "part content must be terminated by the boundary");
            parts.Add(new Part(headers, body[contentStart..contentEnd]));
            pos = contentEnd + 2; // land on "--boundary"
        }
        return parts;
    }

    private static int IndexOf(byte[] haystack, byte[] needle, int from)
    {
        for (int i = from; i <= haystack.Length - needle.Length; i++)
        {
            bool hit = true;
            for (int j = 0; j < needle.Length; j++)
                if (haystack[i + j] != needle[j]) { hit = false; break; }
            if (hit) return i;
        }
        return -1;
    }
}

public class JiraAttachmentClientTests
{
    private const string Email = "filesbot@example.com";
    private const string Token = "dummy-jira-api-token-6120"; // dummy; must never leak
    private static readonly string Basic =
        "Basic " + Convert.ToBase64String(Encoding.UTF8.GetBytes($"{Email}:{Token}"));

    private static string Bean(string id, string filename, long size, string mimeType, string baseUrl) =>
        $$"""
        {
          "id": "{{id}}",
          "self": "{{baseUrl}}/rest/api/3/attachments/{{id}}",
          "filename": "{{filename}}",
          "author": {"accountId": "5b10a2844c20165700ede21g", "displayName": "Files Bot", "active": true},
          "created": "2026-07-16T09:12:44.000+0000",
          "size": {{size}},
          "mimeType": "{{mimeType}}",
          "content": "{{baseUrl}}/rest/api/3/attachment/content/{{id}}"
        }
        """;

    private static byte[] BinaryBlob()
    {
        var blob = new byte[300];
        for (int i = 0; i < blob.Length; i++) blob[i] = (byte)(i % 256);
        return blob;
    }

    [Fact]
    public async Task Upload_PostsTheDocumentedMultipartRequest()
    {
        byte[] csv = Encoding.UTF8.GetBytes("id,total\n1,9.50\n2,12.25\n");
        byte[] blob = BinaryBlob();
        using var mock = new MockServer((n, req) => new MockServer.Scripted(
            200, "[" + Bean("10001", "incident-log.csv", 24, "text/csv", "https://example.atlassian.net")
               + "," + Bean("10002", "heap dump.bin", 300, "application/octet-stream", "https://example.atlassian.net")
               + "]"));

        var client = new JiraAttachmentClient(mock.BaseUrl, Email, Token);
        var report = await client.UploadAsync("OPS-7", new[]
        {
            new AttachmentFile("incident-log.csv", "text/csv", csv),
            new AttachmentFile("heap dump.bin", "application/octet-stream", blob),
        });

        Assert.Single(mock.Requests);
        var req = mock.Requests[0];
        Assert.Equal("POST", req.Method);
        Assert.Equal("/rest/api/3/issue/OPS-7/attachments", req.Path);
        Assert.Equal(Basic, req.Headers.GetValueOrDefault("authorization"));
        // Without this exact header the request is blocked by XSRF protection.
        Assert.Equal("no-check", req.Headers.GetValueOrDefault("x-atlassian-token"));
        string contentType = req.Headers.GetValueOrDefault("content-type") ?? "";
        Assert.StartsWith("multipart/form-data", contentType);

        var parts = Multipart.Parse(req.Body, contentType);
        Assert.Equal(2, parts.Count);
        foreach (var part in parts)
        {
            string disposition = part.Headers.GetValueOrDefault("content-disposition") ?? "";
            Assert.Contains("form-data", disposition);
            // The documented form parameter name for every file is exactly "file",
            // in the RFC-style quoted form the official curl example produces.
            Assert.Contains("name=\"file\"", disposition);
        }
        Assert.Contains("filename=\"incident-log.csv\"", parts[0].Headers["content-disposition"]);
        Assert.Equal("text/csv", parts[0].Headers.GetValueOrDefault("content-type"));
        Assert.Equal(csv, parts[0].Content);
        Assert.Contains("filename=\"heap dump.bin\"", parts[1].Headers["content-disposition"]);
        Assert.Equal("application/octet-stream", parts[1].Headers.GetValueOrDefault("content-type"));
        Assert.Equal(blob, parts[1].Content);

        Assert.False(report.HasMissing);
        Assert.Empty(report.Missing);
        Assert.Equal(2, report.Uploaded.Count);
        Assert.Equal("10001", report.Uploaded[0].Id);
        Assert.Equal("incident-log.csv", report.Uploaded[0].Filename);
        Assert.Equal(24, report.Uploaded[0].Size);
        Assert.Equal("text/csv", report.Uploaded[0].MimeType);
        Assert.Equal("https://example.atlassian.net/rest/api/3/attachment/content/10001",
            report.Uploaded[0].ContentUrl);
        Assert.Equal("10002", report.Uploaded[1].Id);
        Assert.Equal(300, report.Uploaded[1].Size);
        Assert.Equal(324, report.TotalBytes);
    }

    [Fact]
    public async Task Upload_ReportsFilesMissingFromThePartialResponse()
    {
        using var mock = new MockServer((n, req) => new MockServer.Scripted(
            200, "[" + Bean("11001", "a.txt", 3, "text/plain", "https://example.atlassian.net")
               + "," + Bean("11003", "c.txt", 5, "text/plain", "https://example.atlassian.net")
               + "]"));

        var client = new JiraAttachmentClient(mock.BaseUrl, Email, Token);
        var report = await client.UploadAsync("OPS-8", new[]
        {
            new AttachmentFile("a.txt", "text/plain", Encoding.UTF8.GetBytes("aaa")),
            new AttachmentFile("diagram.svg", "image/svg+xml", Encoding.UTF8.GetBytes("<svg/>")),
            new AttachmentFile("c.txt", "text/plain", Encoding.UTF8.GetBytes("ccccc")),
        });

        var parts = Multipart.Parse(mock.Requests[0].Body,
            mock.Requests[0].Headers.GetValueOrDefault("content-type") ?? "");
        Assert.Equal(3, parts.Count);

        Assert.True(report.HasMissing);
        Assert.Equal(new[] { "diagram.svg" }, report.Missing);
        Assert.Equal(2, report.Uploaded.Count);
        Assert.Equal(new[] { "a.txt", "c.txt" }, report.Uploaded.Select(u => u.Filename).ToArray());
        Assert.Equal(8, report.TotalBytes);
    }

    [Fact]
    public async Task Upload_RejectsMoreThanSixtyFilesBeforeAnyRequest()
    {
        using var mock = new MockServer((n, req) => new MockServer.Scripted(200, "[]"));
        var client = new JiraAttachmentClient(mock.BaseUrl, Email, Token);
        var files = Enumerable.Range(0, 61)
            .Select(i => new AttachmentFile($"chunk-{i:D2}.log", "text/plain", new byte[] { 0x41 }))
            .ToArray();

        var ex = await Assert.ThrowsAsync<ArgumentException>(() => client.UploadAsync("OPS-9", files));
        Assert.Contains("60", ex.Message);
        Assert.Empty(mock.Requests);
    }

    [Fact]
    public async Task Upload_SurfacesTheErrorCollectionOnFailure()
    {
        using var mock = new MockServer((n, req) => new MockServer.Scripted(
            413, "{\"errorMessages\":[\"The attachment is too large to upload. The maximum size is"
               + " 10 MB.\"],\"errors\":{}}"));

        var client = new JiraAttachmentClient(mock.BaseUrl, Email, Token);
        var ex = await Assert.ThrowsAsync<JiraApiError>(() => client.UploadAsync("OPS-7", new[]
        {
            new AttachmentFile("huge.iso", "application/octet-stream", new byte[] { 1, 2, 3 }),
        }));

        Assert.Equal(413, ex.StatusCode);
        Assert.Equal(new[] { "The attachment is too large to upload. The maximum size is 10 MB." },
            ex.Messages);
        Assert.Contains("too large", ex.Message);
        Assert.DoesNotContain(Token, ex.Message);
        Assert.DoesNotContain(Email, ex.Message);
        Assert.DoesNotContain(Basic.Split(' ')[1], ex.Message);
    }

    [Fact]
    public async Task Download_FollowsTheRedirectWithoutForwardingCredentials()
    {
        byte[] blob = BinaryBlob();
        using var media = new MockServer((n, req) => new MockServer.Scripted(
            200, Bytes: blob, ContentType: "application/octet-stream"));
        using var jira = new MockServer((n, req) => new MockServer.Scripted(
            302, Headers: new Dictionary<string, string> { ["Location"] = media.BaseUrl + "/media/blob-77" }));

        var client = new JiraAttachmentClient(jira.BaseUrl, Email, Token);
        byte[] got = await client.DownloadAsync(jira.BaseUrl + "/rest/api/3/attachment/content/10002");

        Assert.Equal(blob, got);
        Assert.Single(jira.Requests);
        Assert.Equal("/rest/api/3/attachment/content/10002", jira.Requests[0].Path);
        Assert.Equal(Basic, jira.Requests[0].Headers.GetValueOrDefault("authorization"));

        Assert.Single(media.Requests);
        Assert.Equal("/media/blob-77", media.Requests[0].Path);
        Assert.False(media.Requests[0].Headers.ContainsKey("authorization"),
            "basic credentials must not be forwarded across the redirect to another host");
        Assert.False(media.Requests[0].Headers.ContainsKey("x-atlassian-token"),
            "the XSRF bypass header has no business on the media host");
    }

    [Fact]
    public async Task Download_SurfacesHttpFailures()
    {
        using var mock = new MockServer((n, req) => new MockServer.Scripted(
            404, "{\"errorMessages\":[\"The attachment is not found.\"],\"errors\":{}}"));

        var client = new JiraAttachmentClient(mock.BaseUrl, Email, Token);
        var ex = await Assert.ThrowsAsync<JiraApiError>(
            () => client.DownloadAsync(mock.BaseUrl + "/rest/api/3/attachment/content/404"));
        Assert.Equal(404, ex.StatusCode);
        Assert.Contains("not found", ex.Message);
    }
}
