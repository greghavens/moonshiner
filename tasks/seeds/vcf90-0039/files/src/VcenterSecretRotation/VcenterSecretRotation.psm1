#Requires -Version 7.2

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Contract = $null

function Get-Contract {
    if ($null -eq $script:Contract) {
        $path = Join-Path $PSScriptRoot '../../docs/contract.json'
        $script:Contract = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    }
    return $script:Contract
}

function ConvertTo-ContractPayload {
    <#
    .SYNOPSIS
        Turns a schema-shaped object into the JSON property bag that goes on the wire.
    #>
    param([Parameter(Mandatory)] $Model)

    return $Model | ConvertTo-Json -Depth 20 -Compress | ConvertFrom-Json
}

function Invoke-VcenterApi {
    <#
    .SYNOPSIS
        Sends one contracted request and returns the decoded response.
    #>
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $Method,
        [Parameter(Mandatory)][string] $Path,
        [hashtable] $Query,
        [hashtable] $Headers,
        [string] $Body,
        [switch] $SkipCertificateCheck
    )

    $uri = $BaseUri.TrimEnd('/') + $Path
    if ($Query -and $Query.Count -gt 0) {
        $pairs = foreach ($key in $Query.Keys) {
            '{0}={1}' -f [uri]::EscapeDataString($key), [uri]::EscapeDataString([string]$Query[$key])
        }
        $uri = $uri + '?' + ($pairs -join '&')
    }

    $arguments = @{
        Uri                  = $uri
        Method               = $Method
        Headers              = ($Headers ?? @{})
        MaximumRedirection   = 0
        SkipCertificateCheck = [bool]$SkipCertificateCheck
    }
    if ($PSBoundParameters.ContainsKey('Body') -and $Body) {
        $arguments['Body'] = $Body
        $arguments['ContentType'] = (Get-Contract).api.request_content_type
    }
    return Invoke-RestMethod @arguments
}

function New-VcenterSession {
    <#
    .SYNOPSIS
        Cis.Session_create - exchanges a credential for a session token.
    #>
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][pscredential] $Credential,
        [switch] $SkipCertificateCheck
    )

    $plain = $Credential.GetNetworkCredential().Password
    $raw = '{0}:{1}' -f $Credential.UserName, $plain
    $basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($raw))

    return Invoke-VcenterApi -BaseUri $BaseUri -Method 'POST' -Path '/session' `
        -Headers @{ Authorization = "Basic $basic" } `
        -SkipCertificateCheck:$SkipCertificateCheck
}

function Get-VcenterSessionInfo {
    <#
    .SYNOPSIS
        Cis.Session_get - reports who a session token belongs to.
    #>
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $SessionId,
        [switch] $SkipCertificateCheck
    )

    return Invoke-VcenterApi -BaseUri $BaseUri -Method 'GET' -Path '/session' `
        -Headers @{ 'vmware-api-session-id' = $SessionId } `
        -SkipCertificateCheck:$SkipCertificateCheck
}

function Remove-VcenterSession {
    <#
    .SYNOPSIS
        Cis.Session_delete - retires a session token.
    #>
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $SessionId,
        [switch] $SkipCertificateCheck
    )

    Invoke-VcenterApi -BaseUri $BaseUri -Method 'DELETE' -Path '/session' `
        -Headers @{ 'vmware-api-session-id' = $SessionId } `
        -SkipCertificateCheck:$SkipCertificateCheck | Out-Null
}

function Get-InFlightTask {
    <#
    .SYNOPSIS
        Cis.Tasks_list - the tasks this account still has running.
    #>
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $SessionId,
        [Parameter(Mandatory)][string] $Account,
        [Parameter(Mandatory)][string[]] $Service,
        [switch] $SkipCertificateCheck
    )

    $nonTerminal = (Get-Contract).schemas.'Cis.Task.Status'.non_terminal
    $filterSpec = [pscustomobject][ordered]@{
        tasks      = $null
        services   = $Service
        operations = $null
        status     = $nonTerminal
        targets    = $null
        users      = @($Account)
    }
    $payload = @{ filter_spec = (ConvertTo-ContractPayload -Model $filterSpec) } | ConvertTo-Json -Depth 8 -Compress

    $response = Invoke-VcenterApi -BaseUri $BaseUri -Method 'POST' -Path '/cis/tasks' `
        -Query @{ action = 'list' } `
        -Headers @{ 'vmware-api-session-id' = $SessionId } `
        -Body $payload `
        -SkipCertificateCheck:$SkipCertificateCheck

    if ($null -eq $response) { return @() }
    return @($response.PSObject.Properties.Name)
}

function Get-VcenterTask {
    <#
    .SYNOPSIS
        Cis.Tasks_get - the current state of one task.
    #>
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $SessionId,
        [Parameter(Mandatory)][string] $TaskId,
        [switch] $SkipCertificateCheck
    )

    $getSpec = [pscustomobject][ordered]@{
        return_all     = $null
        exclude_result = $true
    }
    $query = @{}
    foreach ($property in (ConvertTo-ContractPayload -Model $getSpec).PSObject.Properties) {
        $query[$property.Name] = ([string]$property.Value).ToLowerInvariant()
    }

    return Invoke-VcenterApi -BaseUri $BaseUri -Method 'GET' -Path "/cis/tasks/$TaskId" `
        -Query $query `
        -Headers @{ 'vmware-api-session-id' = $SessionId } `
        -SkipCertificateCheck:$SkipCertificateCheck
}

function Invoke-VcenterCredentialRotation {
    <#
    .SYNOPSIS
        Rotates the automation service account onto a new secret.
    .DESCRIPTION
        Opens a session on the new secret, then retires the session that was minted from the
        old secret.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string] $Server,
        [Parameter(Mandatory)][pscredential] $Credential,
        [Parameter(Mandatory)][string] $RetiringSessionId,
        [Parameter(Mandatory)][string[]] $DrainService,
        [int] $DrainTimeoutSeconds = 120,
        [int] $PollIntervalMilliseconds = 1000,
        [switch] $SkipCertificateCheck
    )

    $baseUri = 'https://{0}{1}' -f $Server, (Get-Contract).api.base_path
    $terminal = (Get-Contract).schemas.'Cis.Task.Status'.terminal

    $newSessionId = New-VcenterSession -BaseUri $baseUri -Credential $Credential -SkipCertificateCheck:$SkipCertificateCheck

    $info = Get-VcenterSessionInfo -BaseUri $baseUri -SessionId $newSessionId -SkipCertificateCheck:$SkipCertificateCheck
    Write-Verbose ("new session belongs to {0}" -f $info.user)

    Remove-VcenterSession -BaseUri $baseUri -SessionId $RetiringSessionId -SkipCertificateCheck:$SkipCertificateCheck

    $pending = Get-InFlightTask -BaseUri $baseUri -SessionId $RetiringSessionId -Account $Credential.UserName `
        -Service $DrainService -SkipCertificateCheck:$SkipCertificateCheck

    $drained = [System.Collections.Generic.List[object]]::new()
    $deadline = [datetime]::UtcNow.AddSeconds($DrainTimeoutSeconds)
    while ($pending.Count -gt 0) {
        if ([datetime]::UtcNow -ge $deadline) {
            throw "Timed out draining in-flight tasks: $($pending -join ', ')"
        }
        $stillPending = [System.Collections.Generic.List[string]]::new()
        foreach ($taskId in $pending) {
            $task = Get-VcenterTask -BaseUri $baseUri -SessionId $RetiringSessionId -TaskId $taskId -SkipCertificateCheck:$SkipCertificateCheck
            if ($terminal -contains $task.status) {
                $drained.Add([pscustomobject]@{ TaskId = $taskId; FinalStatus = $task.status })
            }
            else {
                $stillPending.Add($taskId)
            }
        }
        $pending = $stillPending.ToArray()
        if ($pending.Count -gt 0) {
            Start-Sleep -Milliseconds $PollIntervalMilliseconds
        }
    }

    return [pscustomobject]@{
        Account                = $Credential.UserName
        NewSessionId           = $newSessionId
        DrainedTasks           = $drained.ToArray()
        RetiringSessionDeleted = $true
    }
}

Export-ModuleMember -Function 'Invoke-VcenterCredentialRotation'
