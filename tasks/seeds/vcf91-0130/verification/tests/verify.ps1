$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root (
    'VcfVksNamespaceInventory/VcfVksNamespaceInventory.psd1'
)
$ModulePath = Join-Path $Root (
    'VcfVksNamespaceInventory/VcfVksNamespaceInventory.psm1'
)
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf91-0130-' + [guid]::NewGuid().ToString('N')
)
[IO.Directory]::CreateDirectory($TempRoot) | Out-Null
$PortPath = Join-Path $TempRoot 'port.txt'
$LogPath = Join-Path $TempRoot 'requests.jsonl'
$ScenarioPath = Join-Path $TempRoot 'scenario.json'
$StdoutPath = Join-Path $TempRoot 'mock.stdout'
$StderrPath = Join-Path $TempRoot 'mock.stderr'
$MockProcess = $null
$Transport = $null

function Assert-True {
    param(
        [bool] $Condition,
        [Parameter(Mandatory)]
        [string] $Message
    )
    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

function Assert-Equal {
    param(
        [AllowNull()]
        $Actual,
        [AllowNull()]
        $Expected,
        [Parameter(Mandatory)]
        [string] $Message
    )
    if ($Actual -cne $Expected) {
        throw (
            "ASSERTION FAILED: $Message " +
            "(expected '$Expected', got '$Actual')"
        )
    }
}

function Read-RequestLog {
    if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        return @()
    }
    return @(
        Get-Content -LiteralPath $LogPath |
        Where-Object { $_ -ne '' } |
        ForEach-Object { $_ | ConvertFrom-Json }
    )
}

function New-ClusterFixture {
    param(
        [Parameter(Mandatory)]
        [string] $Namespace,
        [Parameter(Mandatory)]
        [string] $Name,
        [Parameter(Mandatory)]
        [string] $Uid,
        [Parameter(Mandatory)]
        [string] $Version,
        [Parameter(Mandatory)]
        [string] $Phase
    )
    return [ordered]@{
        apiVersion = 'cluster.x-k8s.io/v1beta2'
        kind = 'Cluster'
        metadata = [ordered]@{
            name = $Name
            namespace = $Namespace
            uid = $Uid
        }
        spec = [ordered]@{
            topology = [ordered]@{
                version = $Version
            }
        }
        status = [ordered]@{
            phase = $Phase
        }
    }
}

try {
    $Contract = Get-Content -Raw -LiteralPath $ContractPath | ConvertFrom-Json
    $Sources = Get-Content -Raw -LiteralPath $SourcesPath | ConvertFrom-Json

    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' `
        'official repository'
    Assert-Equal $Sources.repositoryCommitSha `
        'c3f3b52c845dd967cabbc21680e893292077d5ba' `
        'pinned VCF 9.1 repository commit'
    Assert-Equal $Sources.specPath `
        'specifications/vsphere/openapi/automation/vcenter.yaml' `
        'official specification path'
    Assert-Equal $Sources.specBlobSha `
        '8028b0824c4ff3503d05f44814f967938a795c40' `
        'official specification blob'
    Assert-Equal $Sources.specification.version '9.1.0.0' `
        'vSphere API version'
    Assert-Equal @($Sources.operationIds).Count 1 `
        'official operationId count'
    Assert-Equal $Sources.operationIds[0] `
        'Vcenter.Namespaces.User.Instances_list' `
        'official operationId'
    Assert-Equal $Sources.operations[0].repositoryCommitSha `
        $Sources.repositoryCommitSha `
        'operation records the repository commit'
    Assert-Equal $Sources.operations[0].specPath $Sources.specPath `
        'operation records the specification path'
    Assert-Equal $Sources.operations[0].specLine 66261 `
        'operation specification line'

    Assert-Equal $Contract.source.commitSha `
        $Sources.repositoryCommitSha `
        'contract and provenance commits agree'
    Assert-Equal $Contract.source.specPath $Sources.specPath `
        'contract and provenance paths agree'
    Assert-Equal @($Contract.operations).Count 1 `
        'contract vCenter operation count'
    $VcenterOperation = $Contract.operations[0]
    Assert-Equal $VcenterOperation.operationId `
        'Vcenter.Namespaces.User.Instances_list' `
        'contract vCenter operationId'
    Assert-Equal $VcenterOperation.sdkCmdlet `
        'Invoke-VcenterNamespacesUserInstancesList' `
        'contract SDK cmdlet'
    Assert-Equal $VcenterOperation.method 'GET' `
        'contract vCenter method'
    Assert-Equal $VcenterOperation.path `
        '/api/vcenter/namespaces-user/namespaces' `
        'contract vCenter path'
    Assert-Equal (($VcenterOperation.parameters.name) -join ',') `
        'filter,groups' `
        'contract optional vCenter parameters'
    Assert-Equal (($VcenterOperation.effectiveQueryFields.name) -join ',') `
        'username,groups' `
        'contract effective optional query fields'
    Assert-Equal (($Contract.schemas.`
        'Vcenter.Namespaces.User.Instances.Summary'.required) -join ',') `
        'namespace,master_host' `
        'contract namespace summary requirements'

    Assert-Equal @($Contract.kubernetesApi.operations).Count 1 `
        'contract Kubernetes operation count'
    $KubeOperation = $Contract.kubernetesApi.operations[0]
    Assert-Equal $KubeOperation.operationKey `
        'cluster.x-k8s.io/v1beta2:namespaced-clusters:list' `
        'contract Kubernetes operation key'
    Assert-Equal $KubeOperation.method 'GET' `
        'contract Kubernetes method'
    Assert-Equal $KubeOperation.pathTemplate (
        '/apis/cluster.x-k8s.io/v1beta2/' +
        'namespaces/{namespace}/clusters'
    ) `
        'contract Kubernetes path'
    Assert-Equal (($KubeOperation.optionalQueryFields) -join ',') (
        'pretty,allowWatchBookmarks,continue,fieldSelector,' +
        'labelSelector,limit,resourceVersion,resourceVersionMatch,' +
        'sendInitialEvents,timeoutSeconds,watch'
    ) `
        'contract optional Kubernetes list fields'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.RequiredModules).Count 1 `
        'manifest prerequisite count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.vSphere' `
        'VCF PowerCLI SDK prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') `
        'VCF PowerCLI SDK version'
    Assert-Equal (($Manifest.FunctionsToExport) -join ',') (
        'New-VcfVksNamespaceSession,' +
        'Get-VcfVksClusterInventory'
    ) `
        'manifest exports'

    $Tokens = $null
    $ParseErrors = $null
    $Ast = [Management.Automation.Language.Parser]::ParseFile(
        $ModulePath,
        [ref] $Tokens,
        [ref] $ParseErrors
    )
    Assert-Equal @($ParseErrors).Count 0 'module parses without errors'
    $SourceText = Get-Content -Raw -LiteralPath $ModulePath
    foreach ($Required in @(
        'VMware.Sdk.vSphere',
        'Invoke-VcenterNamespacesUserInstancesList',
        'System.Net.Http.HttpRequestMessage',
        'Import-Module'
    )) {
        Assert-True $SourceText.Contains($Required) `
            "production module must name $Required"
    }
    foreach ($Forbidden in @(
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'Start-Process',
        'curl',
        'kubectl',
        'Add-Type'
    )) {
        Assert-True (-not $SourceText.Contains($Forbidden)) `
            "production module must not contain $Forbidden"
    }

    Import-Module $ModulePath -Force -ErrorAction Stop
    $Exports = @(
        Get-Command `
            -Module VcfVksNamespaceInventory `
            -CommandType Function |
            Sort-Object Name |
            ForEach-Object Name
    )
    Assert-Equal ($Exports -join ',') (
        'Get-VcfVksClusterInventory,' +
        'New-VcfVksNamespaceSession'
    ) `
        'runtime exports'

    $RunId = [guid]::NewGuid().ToString('N')
    $Suffix = $RunId.Substring(0, 6).ToLowerInvariant()
    $OldToken = 'old-' + $RunId.Substring(6, 16)
    $FreshToken = 'fresh-' + $RunId.Substring(22, 10)
    $NamespaceAlpha = 'alpha-' + $Suffix
    $NamespaceMiddle = 'middle-' + $Suffix
    $NamespaceZulu = 'zulu-' + $Suffix
    $Namespaces = @(
        $NamespaceZulu,
        $NamespaceAlpha,
        $NamespaceMiddle
    )
    $Clusters = [ordered]@{}
    $Clusters[$NamespaceAlpha] = @(
        (New-ClusterFixture `
            -Namespace $NamespaceAlpha `
            -Name ('zeta-' + $Suffix) `
            -Uid ([guid]::NewGuid().ToString()) `
            -Version 'v1.34.2+vmware.1' `
            -Phase 'Provisioned'),
        (New-ClusterFixture `
            -Namespace $NamespaceAlpha `
            -Name ('beta-' + $Suffix) `
            -Uid ([guid]::NewGuid().ToString()) `
            -Version 'v1.35.0+vmware.2' `
            -Phase 'Provisioned')
    )
    $Clusters[$NamespaceMiddle] = @(
        (New-ClusterFixture `
            -Namespace $NamespaceMiddle `
            -Name ('only-' + $Suffix) `
            -Uid ([guid]::NewGuid().ToString()) `
            -Version 'v1.34.1+vmware.1' `
            -Phase 'ScalingUp')
    )
    $Clusters[$NamespaceZulu] = @(
        (New-ClusterFixture `
            -Namespace $NamespaceZulu `
            -Name ('omega-' + $Suffix) `
            -Uid ([guid]::NewGuid().ToString()) `
            -Version 'v1.35.0+vmware.2' `
            -Phase 'Provisioned'),
        (New-ClusterFixture `
            -Namespace $NamespaceZulu `
            -Name ('delta-' + $Suffix) `
            -Uid ([guid]::NewGuid().ToString()) `
            -Version 'v1.33.6+vmware.1' `
            -Phase 'Provisioned')
    )
    $Scenario = [ordered]@{
        old_token = $OldToken
        fresh_token = $FreshToken
        expiry_namespace = $NamespaceMiddle
        namespaces = $Namespaces
        clusters_by_namespace = $Clusters
    }
    [IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 12 -Compress),
        [Text.UTF8Encoding]::new($false)
    )

    $MockProcess = Start-Process -FilePath 'python3' -ArgumentList @(
        '-B',
        $MockPath,
        $PortPath,
        $LogPath,
        $ContractPath,
        $ScenarioPath
    ) -PassThru -RedirectStandardOutput $StdoutPath `
      -RedirectStandardError $StderrPath

    $Deadline = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $PortPath -PathType Leaf)) {
        if ($MockProcess.HasExited) {
            $Details = Get-Content -Raw -LiteralPath $StderrPath `
                -ErrorAction SilentlyContinue
            throw "loopback mock exited before startup: $Details"
        }
        if ($Deadline.Elapsed.TotalSeconds -gt 10) {
            throw 'timed out waiting for loopback mock startup'
        }
        Start-Sleep -Milliseconds 25
    }
    $Port = [int] (Get-Content -Raw -LiteralPath $PortPath)
    $BaseUrl = "http://127.0.0.1:$Port"

    $Handler = [Net.Http.HttpClientHandler]::new()
    $Handler.AllowAutoRedirect = $false
    $Handler.UseProxy = $false
    $Transport = [Net.Http.HttpClient]::new($Handler, $true)
    $Transport.Timeout = [TimeSpan]::FromSeconds(10)

    $AllowedOperationId = [string] $VcenterOperation.operationId
    $VcenterPath = [string] $VcenterOperation.path
    $OperationInvoker = {
        param(
            [string] $OperationId,
            [hashtable] $Parameters,
            $Server
        )

        if ($OperationId -cne $AllowedOperationId) {
            throw "operation outside contract: $OperationId"
        }
        if ($null -eq $Parameters -or $Parameters.Count -ne 0) {
            throw 'unset filter, username, and groups must all be omitted'
        }
        $Request = [Net.Http.HttpRequestMessage]::new(
            [Net.Http.HttpMethod]::Get,
            ([uri] ([string] $Server.BaseUri + $VcenterPath))
        )
        try {
            $Request.Headers.Accept.ParseAdd('application/json')
            $Added = $Request.Headers.TryAddWithoutValidation(
                'vmware-api-session-id',
                [string] $Server.Token
            )
            if (-not $Added) {
                throw 'could not set SDK session header'
            }
            $Response = $Transport.SendAsync($Request).GetAwaiter().GetResult()
            try {
                $Bytes = $Response.Content.ReadAsByteArrayAsync().
                    GetAwaiter().GetResult()
                if ([int] $Response.StatusCode -ne 200) {
                    $Failure = [Net.Http.HttpRequestException]::new(
                        'vCenter namespace operation failed.',
                        $null,
                        $Response.StatusCode
                    )
                    $Failure.Data['StatusCode'] = [int] $Response.StatusCode
                    throw $Failure
                }
                $Json = [Text.Encoding]::UTF8.GetString($Bytes)
                return $Json | ConvertFrom-Json
            }
            finally {
                $Response.Dispose()
            }
        }
        finally {
            $Request.Dispose()
        }
    }.GetNewClosure()

    $RefreshState = [pscustomobject]@{ Count = 0 }
    $FreshServer = [pscustomobject]@{
        BaseUri = $BaseUrl
        Token = $FreshToken
        Generation = 2
    }
    $RefreshConnection = {
        param(
            $ExpiredServer,
            [string] $ExpiredToken
        )
        if ($ExpiredToken -cne $OldToken) {
            throw 'refresh received an unexpected expired access token'
        }
        if ([string] $ExpiredServer.Token -cne $OldToken) {
            throw 'refresh received an unexpected expired SDK handle'
        }
        $RefreshState.Count++
        return [pscustomobject]@{
            Server = $FreshServer
            AccessToken = $FreshToken
        }
    }.GetNewClosure()
    $OldServer = [pscustomobject]@{
        BaseUri = $BaseUrl
        Token = $OldToken
        Generation = 1
    }

    $Session = New-VcfVksNamespaceSession `
        -Server $OldServer `
        -AccessToken $OldToken `
        -RefreshConnection $RefreshConnection `
        -OperationInvoker $OperationInvoker `
        -HttpClient $Transport
    Assert-Equal @(Read-RequestLog).Count 0 `
        'session construction performs no traffic'

    $First = Get-VcfVksClusterInventory -Session $Session
    $Second = Get-VcfVksClusterInventory -Session $Session

    Assert-Equal $RefreshState.Count 1 `
        'the expired generation is refreshed exactly once'
    Assert-Equal ([string] $Session.Server.Token) $FreshToken `
        'replacement SDK handle persists on the session'
    Assert-Equal ([string] $Session.AccessToken) $FreshToken `
        'replacement Kubernetes token persists on the session'
    Assert-True ($First -is [Collections.IList]) `
        'first result implements IList for read-only inspection'
    Assert-True ([bool] ([Collections.IList] $First).IsReadOnly) `
        'first result collection is read-only'
    Assert-True ([bool] ([Collections.IList] $Second).IsReadOnly) `
        'second result collection is read-only'

    $Expected = @(
        foreach ($Namespace in @(
            $NamespaceAlpha,
            $NamespaceMiddle,
            $NamespaceZulu
        )) {
            foreach ($Cluster in @(
                $Clusters[$Namespace] |
                Sort-Object { [string] $_.metadata.name }
            )) {
                [pscustomobject]@{
                    SupervisorNamespace = $Namespace
                    SupervisorEndpoint = $BaseUrl
                    Name = [string] $Cluster.metadata.name
                    Uid = [string] $Cluster.metadata.uid
                    KubernetesVersion = [string] $Cluster.spec.topology.version
                    Phase = [string] $Cluster.status.phase
                }
            }
        }
    )
    foreach ($Result in @($First, $Second)) {
        Assert-Equal $Result.Count $Expected.Count `
            'complete cluster inventory count'
        for ($Index = 0; $Index -lt $Expected.Count; $Index++) {
            $Actual = $Result[$Index]
            $Wanted = $Expected[$Index]
            Assert-Equal (($Actual.PSObject.Properties.Name) -join ',') (
                'SupervisorNamespace,SupervisorEndpoint,Name,Uid,' +
                'KubernetesVersion,Phase'
            ) `
                "record $Index has exactly the public fields"
            foreach ($Property in @(
                'SupervisorNamespace',
                'SupervisorEndpoint',
                'Name',
                'Uid',
                'KubernetesVersion',
                'Phase'
            )) {
                Assert-Equal $Actual.$Property $Wanted.$Property `
                    "record $Index property $Property"
            }
        }
    }

    $Requests = @(Read-RequestLog)
    Assert-Equal $Requests.Count 9 `
        'exact request count across initial and replacement generations'
    $ExpectedRequests = @(
        [pscustomobject]@{
            Kind = 'vcenter'
            Target = '/api/vcenter/namespaces-user/namespaces'
            Token = $OldToken
            Status = 200
        },
        [pscustomobject]@{
            Kind = 'kube'
            Target = (
                '/apis/cluster.x-k8s.io/v1beta2/namespaces/' +
                $NamespaceAlpha + '/clusters'
            )
            Token = $OldToken
            Status = 200
        },
        [pscustomobject]@{
            Kind = 'kube'
            Target = (
                '/apis/cluster.x-k8s.io/v1beta2/namespaces/' +
                $NamespaceMiddle + '/clusters'
            )
            Token = $OldToken
            Status = 401
        },
        [pscustomobject]@{
            Kind = 'kube'
            Target = (
                '/apis/cluster.x-k8s.io/v1beta2/namespaces/' +
                $NamespaceMiddle + '/clusters'
            )
            Token = $FreshToken
            Status = 200
        },
        [pscustomobject]@{
            Kind = 'kube'
            Target = (
                '/apis/cluster.x-k8s.io/v1beta2/namespaces/' +
                $NamespaceZulu + '/clusters'
            )
            Token = $FreshToken
            Status = 200
        },
        [pscustomobject]@{
            Kind = 'vcenter'
            Target = '/api/vcenter/namespaces-user/namespaces'
            Token = $FreshToken
            Status = 200
        },
        [pscustomobject]@{
            Kind = 'kube'
            Target = (
                '/apis/cluster.x-k8s.io/v1beta2/namespaces/' +
                $NamespaceAlpha + '/clusters'
            )
            Token = $FreshToken
            Status = 200
        },
        [pscustomobject]@{
            Kind = 'kube'
            Target = (
                '/apis/cluster.x-k8s.io/v1beta2/namespaces/' +
                $NamespaceMiddle + '/clusters'
            )
            Token = $FreshToken
            Status = 200
        },
        [pscustomobject]@{
            Kind = 'kube'
            Target = (
                '/apis/cluster.x-k8s.io/v1beta2/namespaces/' +
                $NamespaceZulu + '/clusters'
            )
            Token = $FreshToken
            Status = 200
        }
    )
    for ($Index = 0; $Index -lt $Requests.Count; $Index++) {
        $Request = $Requests[$Index]
        $Wanted = $ExpectedRequests[$Index]
        Assert-Equal $Request.method 'GET' `
            "request $Index method"
        Assert-Equal $Request.rawTarget $Wanted.Target `
            "request $Index raw target"
        Assert-Equal $Request.rawQuery '' `
            "request $Index omits every optional query field and '?'"
        Assert-Equal $Request.accept 'application/json' `
            "request $Index Accept header"
        Assert-Equal $Request.contentType $null `
            "request $Index omits Content-Type"
        Assert-Equal $Request.contentLengthHeader $null `
            "request $Index omits Content-Length"
        Assert-Equal $Request.transferEncoding $null `
            "request $Index omits transfer encoding"
        Assert-Equal $Request.bodyLength 0 `
            "request $Index has a zero-byte body"
        Assert-Equal $Request.bodyHex '' `
            "request $Index body bytes"
        Assert-Equal $Request.status $Wanted.Status `
            "request $Index fixture status"
        if ($Wanted.Kind -ceq 'vcenter') {
            Assert-Equal $Request.operationId `
                'Vcenter.Namespaces.User.Instances_list' `
                "request $Index vCenter operationId"
            Assert-Equal $Request.operationKey $null `
                "request $Index has no Kubernetes operation key"
            Assert-Equal $Request.vmwareApiSessionId $Wanted.Token `
                "request $Index SDK token generation"
            Assert-Equal $Request.authorization $null `
                "request $Index omits Authorization"
        }
        else {
            Assert-Equal $Request.operationId $null `
                "request $Index has no fictional vCenter operationId"
            Assert-Equal $Request.operationKey `
                'cluster.x-k8s.io/v1beta2:namespaced-clusters:list' `
                "request $Index Kubernetes operation key"
            Assert-Equal $Request.authorization `
                ('Bearer ' + $Wanted.Token) `
                "request $Index bearer token generation"
            Assert-Equal $Request.vmwareApiSessionId $null `
                "request $Index omits vCenter session header"
        }
    }
    Assert-Equal $Requests[2].rawTarget $Requests[3].rawTarget `
        'refresh retries the byte-identical interrupted target'
    Assert-Equal @(
        $Requests |
        Where-Object {
            $_.rawTarget -ceq $ExpectedRequests[1].Target
        }
    ).Count 2 `
        'completed first namespace is not repeated during refresh'
    Assert-True ([bool] $Requests[0].collectionReversed) `
        'first vCenter result was reversed'
    Assert-True (-not [bool] $Requests[5].collectionReversed) `
        'second vCenter result used the opposite order'
    Assert-True (
        [bool] $Requests[1].collectionReversed -ne
        [bool] $Requests[6].collectionReversed
    ) `
        'same VKS collection used opposite item order across calls'

    Write-Output 'verification passed'
}
finally {
    if ($null -ne $Transport) {
        $Transport.Dispose()
    }
    if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
        Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
        $MockProcess.WaitForExit(5000) | Out-Null
    }
    Remove-Item -LiteralPath $TempRoot -Recurse -Force `
        -ErrorAction SilentlyContinue
}
