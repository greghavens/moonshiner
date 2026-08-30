param(
    [Parameter(Mandatory)]
    [string] $BaseUri
)

$ErrorActionPreference = 'Stop'
$manifest = Import-PowerShellDataFile (Join-Path $PSScriptRoot 'VcfOperationsNetworks.psd1')
$vcfOpsRequirement = @(@($manifest.RequiredModules) | Where-Object {
    $_ -is [System.Collections.IDictionary] -and $_.ModuleName -eq 'VMware.Sdk.Vcf.Ops'
})
if ($vcfOpsRequirement.Count -ne 1 -or
    [string] $vcfOpsRequirement[0].RequiredVersion -ne '13.4.0.24798382') {
    throw 'The manifest must require VMware.Sdk.Vcf.Ops version 13.4.0.24798382.'
}
Import-Module (Join-Path $PSScriptRoot 'VcfOperationsNetworks.psm1') -Force

$global:VcfNetworksTestSleeps = [System.Collections.Generic.List[int]]::new()
function global:Start-Sleep {
    param(
        [Parameter(Mandatory)]
        [int] $Milliseconds
    )
    $global:VcfNetworksTestSleeps.Add($Milliseconds)
}

$first = Invoke-VcfOperationsNetworksCertificateUpdate `
    -Server $BaseUri `
    -Token 'fixture-token' `
    -CertificateId 'platform certificate/primary' `
    -Certificate 'fixture-certificate-alpha' `
    -PrivateKey 'fixture-key-alpha' `
    -PollIntervalMilliseconds 1

if ($first.status -ne 'SUCCESS' -or $first.id -ne 'update id/0001') {
    throw "Unexpected first terminal response: $($first | ConvertTo-Json -Compress)"
}

$second = Invoke-VcfOperationsNetworksCertificateUpdate `
    -Server $BaseUri `
    -Token 'fixture-token' `
    -CertificateId 'proxy-primary' `
    -Certificate 'fixture-certificate-beta' `
    -PrivateKey 'fixture-key-beta' `
    -Chain 'fixture-chain-beta' `
    -PollIntervalMilliseconds 1

if ($second.status -ne 'SUCCESS' -or $second.id -ne 'update-0002') {
    throw "Unexpected second terminal response: $($second | ConvertTo-Json -Compress)"
}

$third = Invoke-VcfOperationsNetworksCertificateUpdate `
    -Server $BaseUri `
    -Token 'fixture-token' `
    -CertificateId 'empty-chain-target' `
    -Certificate 'fixture-certificate-gamma' `
    -PrivateKey 'fixture-key-gamma' `
    -Chain '' `
    -PollIntervalMilliseconds 1

if ($third.status -ne 'SUCCESS' -or $third.id -ne 'update-0003') {
    throw "Unexpected third terminal response: $($third | ConvertTo-Json -Compress)"
}

$failureMessage = $null
try {
    Invoke-VcfOperationsNetworksCertificateUpdate `
        -Server $BaseUri `
        -Token 'fixture-token' `
        -CertificateId 'failed-target' `
        -Certificate 'fixture-certificate-failure' `
        -PrivateKey 'fixture-key-failure' `
        -PollIntervalMilliseconds 1
    throw 'Expected the FAILED terminal status to raise an error.'
}
catch {
    $failureMessage = $_.Exception.Message
}

if ($failureMessage -notlike '*fixture certificate update failed*') {
    throw "Failure did not preserve the service error message: $failureMessage"
}
if (($global:VcfNetworksTestSleeps -join ',') -ne '1,1,1,1,1,1') {
    throw "Polling waits were not limited to nonterminal responses: $($global:VcfNetworksTestSleeps -join ',')"
}

[ordered]@{
    first_status = $first.status
    second_status = $second.status
    third_status = $third.status
    failure_message = $failureMessage
    sleeps = @($global:VcfNetworksTestSleeps)
} | ConvertTo-Json -Compress
