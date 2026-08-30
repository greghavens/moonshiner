[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $ConfigPath,

    [Parameter(Mandatory)]
    [string] $ContractPath,

    [Parameter(Mandatory)]
    [string] $LogPath,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

public sealed class ContractLogForwarderHandler : HttpMessageHandler
{
    private readonly Dictionary<string, string> routes;
    private readonly JsonElement config;
    private readonly string logPath;
    private readonly object logLock = new object();

    public ContractLogForwarderHandler(
        Dictionary<string, string> routes,
        string configJson,
        string logPath)
    {
        this.routes = routes;
        using (JsonDocument document = JsonDocument.Parse(configJson))
        {
            this.config = document.RootElement.Clone();
        }
        this.logPath = logPath;
        File.WriteAllText(logPath, String.Empty, new UTF8Encoding(false));
    }

    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(Handle(request));
    }

    protected override HttpResponseMessage Send(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return Handle(request);
    }

    private HttpResponseMessage Handle(HttpRequestMessage request)
    {
        string rawTarget = request.RequestUri.PathAndQuery;
        string routeKey = request.Method.Method + " " +
            request.RequestUri.AbsolutePath;
        string operationId = null;
        this.routes.TryGetValue(routeKey, out operationId);

        byte[] raw = request.Content == null
            ? Array.Empty<byte>()
            : request.Content.ReadAsByteArrayAsync()
                .GetAwaiter().GetResult();
        Dictionary<string, List<string>> headers =
            new Dictionary<string, List<string>>(StringComparer.Ordinal);
        foreach (var header in request.Headers)
        {
            headers[header.Key.ToLowerInvariant()] = header.Value.ToList();
        }
        if (request.Content != null)
        {
            foreach (var header in request.Content.Headers)
            {
                headers[header.Key.ToLowerInvariant()] =
                    header.Value.ToList();
            }
        }

        int status;
        object responseValue;
        bool committed;
        if (operationId == "getAllLogForwarders")
        {
            status = 200;
            committed = false;
            if (GetString("scenario") == "collision")
            {
                responseValue = new object[]
                {
                    new Dictionary<string, object>
                    {
                        ["id"] = GetString("existing_id"),
                        ["name"] = GetString("name"),
                        ["host"] = GetString("host")
                    }
                };
            }
            else
            {
                responseValue = Array.Empty<object>();
            }
        }
        else if (operationId == "createLogForwarder")
        {
            status = 201;
            committed = true;
            responseValue = new Dictionary<string, object>
            {
                ["id"] = GetString("created_id"),
                ["enabled"] = GetBoolean("enabled"),
                ["host"] = GetString("host"),
                ["name"] = GetString("name"),
                ["port"] = GetInt32("port"),
                ["protocol"] = GetString("protocol"),
                ["sslEnabled"] = GetBoolean("ssl_enabled"),
                ["transportProtocol"] = GetString("transport_protocol")
            };
        }
        else
        {
            status = 404;
            committed = false;
            responseValue = new Dictionary<string, object>
            {
                ["errorCode"] = "OUTSIDE_FOCUSED_CONTRACT"
            };
        }

        object body = null;
        if (raw.Length != 0)
        {
            using (JsonDocument document = JsonDocument.Parse(raw))
            {
                body = document.RootElement.Clone();
            }
        }
        string query = request.RequestUri.Query;
        if (query.StartsWith("?", StringComparison.Ordinal))
        {
            query = query.Substring(1);
        }
        var entry = new Dictionary<string, object>
        {
            ["method"] = request.Method.Method,
            ["raw_target"] = rawTarget,
            ["path"] = request.RequestUri.AbsolutePath,
            ["query"] = query,
            ["headers"] = headers,
            ["body"] = body,
            ["body_raw"] = Encoding.UTF8.GetString(raw),
            ["body_bytes"] = raw.Length,
            ["operationId"] = operationId,
            ["response_status"] = status,
            ["effect_committed"] = committed
        };
        AppendDurably(JsonSerializer.Serialize(entry) + "\n");

        string responseJson = JsonSerializer.Serialize(responseValue);
        var response = new HttpResponseMessage((HttpStatusCode)status);
        response.Content = new StringContent(
            responseJson,
            new UTF8Encoding(false),
            "application/json");
        return response;
    }

    private string GetString(string name)
    {
        return this.config.GetProperty(name).GetString();
    }

    private bool GetBoolean(string name)
    {
        return this.config.GetProperty(name).GetBoolean();
    }

    private int GetInt32(string name)
    {
        return this.config.GetProperty(name).GetInt32();
    }

    private void AppendDurably(string text)
    {
        byte[] bytes = new UTF8Encoding(false).GetBytes(text);
        lock (this.logLock)
        {
            using (var stream = new FileStream(
                this.logPath,
                FileMode.Append,
                FileAccess.Write,
                FileShare.ReadWrite))
            {
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
        }
    }
}
'@

$FilesRoot = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path (
    Join-Path $FilesRoot 'VcfOpsLogForwarder'
) 'VcfOpsLogForwarder.psm1'
$ConfigRaw = Get-Content -LiteralPath $ConfigPath -Raw
$Config = $ConfigRaw | ConvertFrom-Json -AsHashtable
$Contract = Get-Content -LiteralPath $ContractPath -Raw |
    ConvertFrom-Json -AsHashtable

$Routes = [Collections.Generic.Dictionary[string, string]]::new(
    [StringComparer]::Ordinal
)
foreach ($Operation in $Contract.operations) {
    $RouteKey = "$($Operation.method) $($Operation.path)"
    $Routes.Add($RouteKey, $Operation.operationId)
}
if (
    $Routes.Count -ne 2 -or
    -not $Routes.ContainsValue('getAllLogForwarders') -or
    -not $Routes.ContainsValue('createLogForwarder')
) {
    throw 'The focused contract operation set changed.'
}

Import-Module $ModulePath -Force -ErrorAction Stop

$Handler = [ContractLogForwarderHandler]::new(
    $Routes,
    $ConfigRaw,
    $LogPath
)
$HttpClient = [Net.Http.HttpClient]::new($Handler, $false)
try {
    try {
        $Result = New-VcfOpsLogForwarderIfAbsent `
            -BaseUri ([uri] 'http://127.0.0.1/') `
            -LogToken $Config.log_token `
            -Name $Config.name `
            -Host $Config.host `
            -Port $Config.port `
            -Protocol $Config.protocol `
            -SslEnabled $Config.ssl_enabled `
            -TransportProtocol $Config.transport_protocol `
            -Enabled $Config.enabled `
            -HttpClient $HttpClient

        $Envelope = [ordered] @{
            ok = $true
            result = $Result
        }
    }
    catch {
        $Envelope = [ordered] @{
            ok = $false
            errorType = $_.Exception.GetType().FullName
            message = $_.Exception.Message
        }
    }

    $Envelope |
        ConvertTo-Json -Depth 20 -Compress |
        Set-Content `
            -LiteralPath $OutputPath `
            -Encoding utf8NoBOM `
            -NoNewline
}
finally {
    $HttpClient.Dispose()
    $Handler.Dispose()
}
