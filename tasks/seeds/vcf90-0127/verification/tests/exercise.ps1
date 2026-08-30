param(
    [Parameter(Mandatory)]
    [uri] $BaseUri,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

$ErrorActionPreference = 'Stop'
$null = Import-Module VMware.OpenAPI -RequiredVersion 13.4.0.24798382 -Force
$modulePath = Join-Path $PSScriptRoot '../src/Vcf.OperationsNetworks/Vcf.OperationsNetworks.psd1'
Import-Module $modulePath -Force

$securePassword = ConvertTo-SecureString 'test-password' -AsPlainText -Force
$credential = [pscredential]::new('netops@example.com', $securePassword)

$vcenters = @(
    [pscustomobject] [ordered] @{
        fqdn = 'vc-a.lab.example'
        proxy_id = '10000:901:collector-a'
        nickname = 'vc-a'
        enabled = $false
        notes = $null
        credentials = [pscustomobject] [ordered] @{
            username = 'svc-a@vsphere.local'
            password = 'vc-a-password'
            internal_note = 'must-not-leak'
        }
        ipfix_request = [pscustomobject] [ordered] @{
            enable_all = $false
            disable_all = $null
            enable_for_dvs = ''
            dvpgs_ipfix_request = @()
            internal_note = 'must-not-leak'
        }
        tags = @(
            [pscustomobject] [ordered] @{
                tag_key = 'environment'
                tag_value = 'production'
                internal_note = 'must-not-leak'
            }
        )
    },
    [pscustomobject] [ordered] @{
        ip = '192.0.2.42'
        fqdn = ''
        proxy_id = '10000:901:collector-b'
        nickname = 'vc-b'
        credentials = [pscustomobject] [ordered] @{
            username = 'svc-b@vsphere.local'
            password = 'vc-b-password'
        }
        ipfix_request = $null
        tags = @()
    }
)

$global:VcfNetworksInitializerCalls = 0
$initializerBreakpoint = Set-PSBreakpoint `
    -Command Initialize-VcfOpsUsernamePassword `
    -Action { $global:VcfNetworksInitializerCalls++ }
try {
    $created = @(Add-VcfOperationsNetworksVCenterBatch `
        -BaseUri $BaseUri `
        -Credential $credential `
        -VCenter $vcenters)
}
finally {
    Remove-PSBreakpoint -Breakpoint $initializerBreakpoint
}
if ($global:VcfNetworksInitializerCalls -lt 1) {
    throw 'The SDK credential initializer was not called.'
}

ConvertTo-Json -InputObject $created -Depth 12 -Compress |
    Set-Content -LiteralPath $OutputPath -Encoding utf8NoBOM
