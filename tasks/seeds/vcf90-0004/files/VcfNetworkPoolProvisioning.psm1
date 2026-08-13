#Requires -Version 7.0

Set-StrictMode -Version Latest

$script:RetryableStatusCodes = @(429, 502, 503, 504)

function Get-VcfProvisionStatusCode {
    <#
        Walk the exception chain for the HTTP status the SDK failed on. The
        generated client raises ApiException with the status in ErrorCode.
    #>
    param([Parameter(Mandatory)][object]$ErrorRecord)

    $exception = $ErrorRecord.Exception
    while ($null -ne $exception) {
        $code = $null
        $property = $exception.PSObject.Properties['ErrorCode']
        if ($null -ne $property -and $property.Value -is [int]) {
            $code = [int]$property.Value
        }
        if ($null -eq $code) {
            $property = $exception.PSObject.Properties['StatusCode']
            if ($null -ne $property -and $null -ne $property.Value) {
                $code = [int]$property.Value
            }
        }
        if ($null -ne $code -and $code -ge 100 -and $code -le 599) {
            return $code
        }
        $exception = $exception.InnerException
    }

    return 0
}

function ConvertTo-VcfProvisionNetworkModel {
    param([Parameter(Mandatory)][hashtable]$Definition)

    $arguments = @{
        Type    = [string]$Definition.Type
        VlanId  = [int]$Definition.VlanId
        Mtu     = [int]$Definition.Mtu
        Subnet  = [string]$Definition.Subnet
        Mask    = [string]$Definition.Mask
        Gateway = [string]$Definition.Gateway
    }

    if ($Definition.ContainsKey('IpPools')) {
        $ranges = @($Definition.IpPools)
        if ($ranges.Count -gt 0) {
            $pools = [System.Collections.Generic.List[object]]::new()
            foreach ($range in $ranges) {
                $pools.Add((Initialize-VcfIpPool -Start ([string]$range.Start) -VarEnd ([string]$range.End)))
            }
            $arguments['IpPools'] = $pools
        }
    }

    return Initialize-VcfNetwork @arguments
}

<#
.SYNOPSIS
    Provision an SDDC Manager network pool.

.DESCRIPTION
    Looks the pool up once up front, then creates it and retries the create if
    the call fails.
#>
function Invoke-VcfNetworkPoolProvision {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$Server,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string]$Name,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [hashtable[]]$Network,

        [ValidateRange(1, 10)]
        [int]$MaxAttempts = 3,

        [ValidateRange(0, 300)]
        [int]$RetryDelaySeconds = 5,

        [scriptblock]$SleepAction
    )

    $networks = [System.Collections.Generic.List[object]]::new()
    foreach ($definition in $Network) {
        $networks.Add((ConvertTo-VcfProvisionNetworkModel -Definition $definition))
    }
    $spec = Initialize-VcfNetworkPool -Name $Name -Networks $networks

    $page = Invoke-VcfGetNetworkPool -Server $Server
    $elements = if ($null -eq $page -or $null -eq $page.Elements) { @() } else { @($page.Elements) }
    $existing = @($elements | Where-Object { [string]$_.Name -ceq $Name })
    if ($existing.Count -ge 1) {
        return [pscustomobject]@{
            Name     = $Name
            PoolId   = [string]$existing[0].Id
            Outcome  = 'AlreadyExists'
            Attempts = 1
            Pool     = $existing[0]
        }
    }

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            $created = Invoke-VcfCreateNetworkPool -Server $Server -NetworkPool $spec
            return [pscustomobject]@{
                Name     = $Name
                PoolId   = [string]$created.Id
                Outcome  = 'Created'
                Attempts = $attempt
                Pool     = $created
            }
        }
        catch {
            $status = Get-VcfProvisionStatusCode -ErrorRecord $_
            if ($script:RetryableStatusCodes -notcontains $status -or $attempt -ge $MaxAttempts) {
                throw
            }
            if ($PSBoundParameters.ContainsKey('SleepAction') -and $null -ne $SleepAction) {
                & $SleepAction $RetryDelaySeconds
            }
            elseif ($RetryDelaySeconds -gt 0) {
                Start-Sleep -Seconds $RetryDelaySeconds
            }
        }
    }

    throw "Network pool '$Name' could not be provisioned within $MaxAttempts attempts."
}

Export-ModuleMember -Function 'Invoke-VcfNetworkPoolProvision'
