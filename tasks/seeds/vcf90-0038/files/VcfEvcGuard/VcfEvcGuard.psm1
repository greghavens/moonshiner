Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

<#
.SYNOPSIS
    Opens a session against the vSphere Automation API of a VCF 9.0 vCenter.

.DESCRIPTION
    Exchanges the supplied credential for a session token and returns a
    connection object that Invoke-VcfEvcModeGuardedSet consumes.

    See README.md for the exact parameter set, the shape of the returned
    object, and the wire behaviour this must produce.
#>
function Connect-VcfEvcGuardServer {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [System.Management.Automation.PSCredential] $Credential,

        [ValidateRange(1, 65535)]
        [int] $Port = 443,

        [ValidateSet('https', 'http')]
        [string] $Protocol = 'https',

        [switch] $IgnoreInvalidCertificate
    )

    throw [System.NotImplementedException]::new('Connect-VcfEvcGuardServer is not implemented yet.')
}

<#
.SYNOPSIS
    Runs the EVC mode precheck for a cluster and applies the change only when
    the precheck comes back clean.

.DESCRIPTION
    See README.md for the required call sequence, the gating rule, and the
    shape of the returned object.
#>
function Invoke-VcfEvcModeGuardedSet {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [psobject] $Connection,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Cluster,

        [hashtable] $EvcMode,

        [ValidateRange(0.01, 3600)]
        [double] $PollIntervalSeconds = 1,

        [ValidateRange(1, 86400)]
        [double] $TimeoutSeconds = 300
    )

    throw [System.NotImplementedException]::new('Invoke-VcfEvcModeGuardedSet is not implemented yet.')
}

Export-ModuleMember -Function Connect-VcfEvcGuardServer, Invoke-VcfEvcModeGuardedSet
