#nullable enable
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

public sealed class VcfContractFallbackHandler : HttpMessageHandler
{
    private readonly object gate = new object();
    private readonly Dictionary<string, string> routes = new();
    private readonly string logPath;
    private readonly string oldToken;
    private readonly string newToken;
    private readonly JsonObject createdIds;
    private readonly JsonArray forwarders = new();
    private int successfulCreates;
    private bool oldTokenExpired;

    public VcfContractFallbackHandler(
        string contractPath,
        string configPath,
        string logPath)
    {
        this.logPath = logPath;
        var contract = JsonNode.Parse(File.ReadAllText(contractPath))!.AsObject();
        foreach (var pathEntry in contract["paths"]!.AsObject())
        {
            foreach (var methodEntry in pathEntry.Value!.AsObject())
            {
                if (methodEntry.Value is not JsonObject operation ||
                    operation["operationId"] is null)
                {
                    continue;
                }
                routes.Add(
                    methodEntry.Key.ToUpperInvariant() + " " + pathEntry.Key,
                    operation["operationId"]!.GetValue<string>());
            }
        }
        var named = contract["x-source"]!["operationIds"]!.AsArray()
            .Select(node => node!.GetValue<string>())
            .OrderBy(value => value, StringComparer.Ordinal)
            .ToArray();
        var routed = routes.Values
            .OrderBy(value => value, StringComparer.Ordinal)
            .ToArray();
        if (!named.SequenceEqual(routed, StringComparer.Ordinal) || routes.Count != 2)
        {
            throw new InvalidDataException("focused contract route table changed");
        }

        var config = JsonNode.Parse(File.ReadAllText(configPath))!.AsObject();
        oldToken = config["old_token"]!.GetValue<string>();
        newToken = config["new_token"]!.GetValue<string>();
        createdIds = config["created_ids"]!.AsObject();
        foreach (var item in config["initial_forwarders"]!.AsArray())
        {
            forwarders.Add(item!.DeepClone());
        }
        File.WriteAllText(logPath, string.Empty, new UTF8Encoding(false));
    }

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var bodyBytes = request.Content is null
            ? Array.Empty<byte>()
            : await request.Content.ReadAsByteArrayAsync(cancellationToken)
                .ConfigureAwait(false);
        var bodyRaw = Encoding.UTF8.GetString(bodyBytes);
        JsonNode? body = null;
        if (bodyBytes.Length != 0)
        {
            try { body = JsonNode.Parse(bodyRaw); }
            catch (JsonException) { body = null; }
        }

        var path = request.RequestUri!.AbsolutePath;
        var query = request.RequestUri.Query;
        var key = request.Method.Method.ToUpperInvariant() + " " + path;
        routes.TryGetValue(key, out var operationId);
        var headers = CollectHeaders(request);
        var token = SingleHeader(headers, "x-jwt-token");
        int status;
        JsonNode payload;
        lock (gate)
        {
            status = 404;
            payload = Error("API_ERROR", "operation is outside the focused contract");
            if (operationId is not null && query.Length == 0)
            {
                if (operationId == "getAllLogForwarders")
                {
                    (status, payload) = ListForwarders(token);
                }
                else if (operationId == "createLogForwarder")
                {
                    (status, payload) = CreateForwarder(token, body);
                }
            }
            AppendLog(new JsonObject
            {
                ["operationId"] = operationId,
                ["method"] = request.Method.Method,
                ["raw_target"] = path + query,
                ["path"] = path,
                ["query"] = query.StartsWith("?", StringComparison.Ordinal)
                    ? query.Substring(1)
                    : query,
                ["headers"] = headers.DeepClone(),
                ["body_raw"] = bodyRaw,
                ["body_bytes"] = bodyBytes.Length,
                ["body"] = body?.DeepClone(),
                ["status"] = status,
            });
        }

        var responseBytes = Encoding.UTF8.GetBytes(
            payload.ToJsonString(new JsonSerializerOptions { WriteIndented = false }));
        var response = new HttpResponseMessage((HttpStatusCode)status);
        response.Content = new ByteArrayContent(responseBytes);
        response.Content.Headers.ContentType = new MediaTypeHeaderValue(
            "application/json");
        return response;
    }

    private (int, JsonNode) ListForwarders(string? token)
    {
        if (token == oldToken && oldTokenExpired)
        {
            return (403, AuthError());
        }
        if (token != oldToken && token != newToken)
        {
            return (403, AuthError());
        }
        return (200, forwarders.DeepClone());
    }

    private (int, JsonNode) CreateForwarder(string? token, JsonNode? body)
    {
        if (token == oldToken)
        {
            if (successfulCreates >= 1)
            {
                oldTokenExpired = true;
                return (403, AuthError());
            }
        }
        else if (token != newToken)
        {
            return (403, AuthError());
        }
        if (body is not JsonObject candidate)
        {
            return (400, Error("JSON_FORMAT_ERROR", "body must be an object"));
        }
        var name = candidate["name"]?.GetValue<string>();
        if (string.IsNullOrEmpty(name))
        {
            return (400, Error("FIELD_ERROR", "name must be nonblank"));
        }
        if (forwarders.Any(item =>
            item?["name"]?.GetValue<string>() == name))
        {
            return (400, Error("FIELD_ERROR", "name already exists"));
        }
        var created = candidate.DeepClone().AsObject();
        created["id"] = createdIds[name!]!.GetValue<string>();
        forwarders.Add(created.DeepClone());
        successfulCreates++;
        return (201, created);
    }

    private static JsonObject AuthError() =>
        Error("SECURITY_ERROR", "access token expired");

    private static JsonObject Error(string code, string message) => new()
    {
        ["errorCode"] = code,
        ["errorMessage"] = message,
    };

    private static JsonObject CollectHeaders(HttpRequestMessage request)
    {
        var result = new JsonObject();
        AddHeaders(result, request.Headers);
        if (request.Content is not null)
        {
            AddHeaders(result, request.Content.Headers);
        }
        return result;
    }

    private static void AddHeaders(JsonObject target, HttpHeaders source)
    {
        foreach (var header in source)
        {
            var name = header.Key.ToLowerInvariant();
            if (target[name] is not JsonArray values)
            {
                values = new JsonArray();
                target[name] = values;
            }
            foreach (var value in header.Value)
            {
                values.Add(value);
            }
        }
    }

    private static string? SingleHeader(JsonObject headers, string name)
    {
        if (headers[name] is not JsonArray values || values.Count != 1)
        {
            return null;
        }
        return values[0]!.GetValue<string>();
    }

    private void AppendLog(JsonObject value)
    {
        var bytes = Encoding.UTF8.GetBytes(
            value.ToJsonString(new JsonSerializerOptions { WriteIndented = false })
            + "\n");
        using var stream = new FileStream(
            logPath,
            FileMode.Append,
            FileAccess.Write,
            FileShare.Read);
        stream.Write(bytes, 0, bytes.Length);
        stream.Flush(flushToDisk: true);
    }
}
