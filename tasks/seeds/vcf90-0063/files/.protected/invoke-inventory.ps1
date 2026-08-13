#!/usr/bin/env pwsh
# Protected driver. Imports the module under test and calls its single public function
# exactly once, then writes the emitted objects to -OutFile as JSON. The verifier reads
# that file together with the mock's request log. This file shows the precise invocation
# the verifier uses; it is not part of the deliverable.

[CmdletBinding()]
param(
    [Parameter(Mandatory)][int]    $Port,
    [Parameter(Mandatory)][string] $OutFile,
    [Parameter(Mandatory)][string] $UserName,
    [Parameter(Mandatory)][string] $Password,
    [int]    $PageSize,
    [string] $AuthSource,
    # Comma separated, because pwsh -File passes every argument as a single string.
    [string] $ResourceIdCsv
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$moduleRoot = Join-Path (Split-Path -Parent (Split-Path -Parent $PSCommandPath)) 'src/VcfOpsAlertInventory'
$manifest   = Join-Path $moduleRoot 'VcfOpsAlertInventory.psd1'

function Write-Result([hashtable] $Payload) {
    $json = $Payload | ConvertTo-Json -Depth 8
    Set-Content -LiteralPath $OutFile -Value $json -Encoding utf8NoBOM
}

try {
    Import-Module -Name $manifest -Force -ErrorAction Stop

    $secure = ConvertTo-SecureString -String $Password -AsPlainText -Force
    $cred   = [System.Management.Automation.PSCredential]::new($UserName, $secure)

    $callArgs = @{
        Server               = '127.0.0.1'
        Port                 = $Port
        Protocol             = 'http'
        Credential           = $cred
        SkipCertificateCheck = $true
    }
    if ($PSBoundParameters.ContainsKey('PageSize'))   { $callArgs['PageSize']   = $PageSize }
    if ($PSBoundParameters.ContainsKey('AuthSource')) { $callArgs['AuthSource'] = $AuthSource }
    if ($PSBoundParameters.ContainsKey('ResourceIdCsv')) {
        $callArgs['ResourceId'] = [string[]] ($ResourceIdCsv -split ',')
    }

    $emitted = @(Get-VcfOpsAlertInventory @callArgs)

    # ConvertTo-Json preserves property order, so the verifier can assert it.
    Write-Result @{
        ok      = $true
        count   = $emitted.Count
        alerts  = $emitted
    }
    exit 0
}
catch {
    Write-Result @{
        ok      = $false
        error   = $_.Exception.Message
        type    = $_.Exception.GetType().FullName
    }
    exit 3
}
