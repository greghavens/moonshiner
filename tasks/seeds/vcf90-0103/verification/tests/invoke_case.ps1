param(
    [Parameter(Mandatory = $true)]
    [string] $BaseUri,

    [Parameter(Mandatory = $true)]
    [string] $OutputPath
)

$ErrorActionPreference = 'Stop'
$manifest = Join-Path $PSScriptRoot '..' 'VcfInstallerTaskInventory' 'VcfInstallerTaskInventory.psd1'
Import-Module $manifest -Force -ErrorAction Stop

$origin = [uri] $BaseUri
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$client = [System.Net.Http.HttpClient]::new($handler, $true)
$breakpoint = $null

function Invoke-SuccessCase {
    param(
        [Parameter(Mandatory = $true)]
        [int] $PageSize,

        [switch] $UseOwnedClient,

        [switch] $OmitPageSize
    )

    $threw = $false
    $items = @()
    try {
        if ($OmitPageSize) {
            $items = @(Get-VcfInstallerTaskInventory -BaseUri $origin)
        }
        elseif ($UseOwnedClient) {
            $items = @(Get-VcfInstallerTaskInventory -BaseUri $origin -PageSize $PageSize)
        }
        else {
            $items = @(
                Get-VcfInstallerTaskInventory -BaseUri $origin -PageSize $PageSize -HttpClient $client
            )
        }
    }
    catch {
        $threw = $true
    }
    return [pscustomobject] [ordered]@{
        Threw = $threw
        Items = $items
    }
}

function Invoke-FailureCase {
    param(
        [Parameter(Mandatory = $true)]
        [int] $PageSize,

        [switch] $UseOwnedClient
    )

    $threw = $false
    $successItems = [System.Collections.Generic.List[object]]::new()
    try {
        if ($UseOwnedClient) {
            Get-VcfInstallerTaskInventory -BaseUri $origin -PageSize $PageSize |
                ForEach-Object { [void] $successItems.Add($_) }
        }
        else {
            Get-VcfInstallerTaskInventory -BaseUri $origin -PageSize $PageSize -HttpClient $client |
                ForEach-Object { [void] $successItems.Add($_) }
        }
    }
    catch {
        $threw = $true
    }
    return [pscustomobject] [ordered]@{
        Threw = $threw
        SuccessCount = $successItems.Count
    }
}

function Invoke-InvalidArgumentCase {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock] $Invocation
    )

    $threw = $false
    $successItems = [System.Collections.Generic.List[object]]::new()
    try {
        & $Invocation | ForEach-Object { [void] $successItems.Add($_) }
    }
    catch {
        $threw = $true
    }
    return [pscustomobject] [ordered]@{
        Threw = $threw
        SuccessCount = $successItems.Count
    }
}

try {
    $global:Vcf90DiscoveryCalls = 0
    $breakpoint = Set-PSBreakpoint -Command Get-VcfInstallerOperation -Action {
        [void] ($global:Vcf90DiscoveryCalls++)
    }
    $first = @(Get-VcfInstallerTaskInventory -BaseUri $origin -PageSize 2 -HttpClient $client)
    $second = @(Get-VcfInstallerTaskInventory -BaseUri $origin -PageSize 2 -HttpClient $client)
    $discoveryCalls = $global:Vcf90DiscoveryCalls
    Remove-PSBreakpoint -Breakpoint $breakpoint
    $breakpoint = $null

    $successCases = [ordered]@{
        DefaultPageSize = Invoke-SuccessCase -PageSize 100 -UseOwnedClient -OmitPageSize
        CaseInsensitiveContentType = Invoke-SuccessCase -PageSize 14
    }

    $failureCases = [ordered]@{}
    $failureCases.Redirect = Invoke-FailureCase -PageSize 3 -UseOwnedClient
    $failureCases.LatePageFailure = Invoke-FailureCase -PageSize 4
    $failureCases.DuplicateWithinPage = Invoke-FailureCase -PageSize 5
    $failureCases.MalformedTimestamp = Invoke-FailureCase -PageSize 6
    $failureCases.BlankRequiredTaskField = Invoke-FailureCase -PageSize 7
    $failureCases.NonIntegerMetadata = Invoke-FailureCase -PageSize 8
    $failureCases.InconsistentTotalPages = Invoke-FailureCase -PageSize 9
    $failureCases.WrongPageNumber = Invoke-FailureCase -PageSize 10
    $failureCases.ShortNonFinalPage = Invoke-FailureCase -PageSize 11
    $failureCases.ChangingTotals = Invoke-FailureCase -PageSize 12
    $failureCases.DuplicateAcrossPages = Invoke-FailureCase -PageSize 13
    $failureCases.UnexpectedStatus = Invoke-FailureCase -PageSize 15
    $failureCases.ElementsNotArray = Invoke-FailureCase -PageSize 16
    $failureCases.MetadataPageSizeMismatch = Invoke-FailureCase -PageSize 17
    $failureCases.IncompleteFinalPage = Invoke-FailureCase -PageSize 18
    $failureCases.InvalidJson = Invoke-FailureCase -PageSize 19
    $failureCases.MissingMetadataField = Invoke-FailureCase -PageSize 20
    $failureCases.NonStringTaskField = Invoke-FailureCase -PageSize 21
    $failureCases.UnexpectedContentType = Invoke-FailureCase -PageSize 22
    $failureCases.NegativeTotal = Invoke-FailureCase -PageSize 23
    $failureCases.TopLevelNotObject = Invoke-FailureCase -PageSize 24
    $failureCases.MetadataNotObject = Invoke-FailureCase -PageSize 25
    $failureCases.TaskNotObject = Invoke-FailureCase -PageSize 26

    $authority = $origin.Authority
    $invalidArguments = [ordered]@{}
    $invalidArguments.RelativeUri = Invoke-InvalidArgumentCase {
        Get-VcfInstallerTaskInventory -BaseUri ([uri] 'relative') -PageSize 2 -HttpClient $client
    }
    $invalidArguments.WrongScheme = Invoke-InvalidArgumentCase {
        Get-VcfInstallerTaskInventory -BaseUri ([uri] 'ftp://example.test/') -PageSize 2 -HttpClient $client
    }
    $invalidArguments.Credentials = Invoke-InvalidArgumentCase {
        Get-VcfInstallerTaskInventory -BaseUri ([uri] "http://user:pass@$authority/") -PageSize 2 -HttpClient $client
    }
    $invalidArguments.Query = Invoke-InvalidArgumentCase {
        Get-VcfInstallerTaskInventory -BaseUri ([uri] ($BaseUri + '/?x=1')) -PageSize 2 -HttpClient $client
    }
    $invalidArguments.Fragment = Invoke-InvalidArgumentCase {
        Get-VcfInstallerTaskInventory -BaseUri ([uri] ($BaseUri + '/#part')) -PageSize 2 -HttpClient $client
    }
    $invalidArguments.NonRootPath = Invoke-InvalidArgumentCase {
        Get-VcfInstallerTaskInventory -BaseUri ([uri] ($BaseUri + '/api')) -PageSize 2 -HttpClient $client
    }
    $invalidArguments.HostlessOrigin = Invoke-InvalidArgumentCase {
        Get-VcfInstallerTaskInventory -BaseUri ([uri] 'http:/') -PageSize 2 -HttpClient $client
    }
    $invalidArguments.PageSizeTooSmall = Invoke-InvalidArgumentCase {
        Get-VcfInstallerTaskInventory -BaseUri $origin -PageSize 0 -HttpClient $client
    }
    $invalidArguments.PageSizeTooLarge = Invoke-InvalidArgumentCase {
        Get-VcfInstallerTaskInventory -BaseUri $origin -PageSize 101 -HttpClient $client
    }

    $inventoryCommand = Get-Command Get-VcfInstallerTaskInventory -ErrorAction Stop
    $commonParameters = @(
        'Verbose', 'Debug', 'ErrorAction', 'WarningAction', 'InformationAction',
        'ProgressAction', 'ErrorVariable', 'WarningVariable', 'InformationVariable',
        'OutVariable', 'OutBuffer', 'PipelineVariable'
    )
    $parameterContract = [ordered]@{
        Names = @(
            $inventoryCommand.Parameters.Keys |
                Where-Object { $_ -notin $commonParameters } |
                Sort-Object
        )
        BaseUriType = $inventoryCommand.Parameters['BaseUri'].ParameterType.FullName
        PageSizeType = $inventoryCommand.Parameters['PageSize'].ParameterType.FullName
        HttpClientType = $inventoryCommand.Parameters['HttpClient'].ParameterType.FullName
        BaseUriMandatory = @(
            $inventoryCommand.Parameters['BaseUri'].Attributes |
                Where-Object {
                    $_ -is [System.Management.Automation.ParameterAttribute] -and
                    $_.Mandatory
                }
        ).Count -gt 0
        PageSizeMandatory = @(
            $inventoryCommand.Parameters['PageSize'].Attributes |
                Where-Object {
                    $_ -is [System.Management.Automation.ParameterAttribute] -and
                    $_.Mandatory
                }
        ).Count -gt 0
        HttpClientMandatory = @(
            $inventoryCommand.Parameters['HttpClient'].Attributes |
                Where-Object {
                    $_ -is [System.Management.Automation.ParameterAttribute] -and
                    $_.Mandatory
                }
        ).Count -gt 0
    }

    $result = [ordered]@{
        First = $first
        Second = $second
        DiscoveryCalls = $discoveryCalls
        SuccessCases = $successCases
        FailureCases = $failureCases
        InvalidArguments = $invalidArguments
        ParameterContract = $parameterContract
        ExportedFunctions = @(
            (Get-Module VcfInstallerTaskInventory).ExportedFunctions.Keys |
                Sort-Object
        )
    }
    $json = $result | ConvertTo-Json -Depth 12 -Compress
    [System.IO.File]::WriteAllText(
        $OutputPath,
        $json,
        [System.Text.UTF8Encoding]::new($false)
    )
}
finally {
    if ($null -ne $breakpoint) {
        Remove-PSBreakpoint -Breakpoint $breakpoint -ErrorAction SilentlyContinue
    }
    Remove-Variable Vcf90DiscoveryCalls -Scope Global -ErrorAction SilentlyContinue
    $client.Dispose()
}
