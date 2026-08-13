# SHIPPED TOKEN PATH - do not modify.
#
# VCF PowerCLI 9.1 publishes generated SDK bindings for SDDC Manager, Installer, Operations
# and Cloud Builder, and none for VCF Automation. The bearer token that the VCF Automation
# calls carry is issued by SDDC Manager (POST /v1/tokens), which IS covered by the SDK, so
# that half of the job is delegated to VMware.Sdk.Vcf.SddcManager rather than reimplemented.
#
# VCF PowerCLI is an environment prerequisite (Install-Module VCF.PowerCLI). It is never
# vendored here, and it is only touched on this code path - Connect-VcfaOrgSession -AccessToken
# skips it entirely, which is what lets the change orchestration be exercised offline.

function Get-VcfaFleetAccessToken {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $SddcManagerServer,

        [Parameter(Mandatory)]
        [pscredential] $Credential
    )

    $sdk = 'VMware.Sdk.Vcf.SddcManager'
    if (-not (Get-Module -Name $sdk -ListAvailable)) {
        throw ("$sdk is not available. It ships with VCF PowerCLI 9.1 and is expected to be " +
               "installed as an environment prerequisite (Install-Module -Name VCF.PowerCLI). " +
               "Alternatively pass an already-issued token to Connect-VcfaOrgSession -AccessToken.")
    }
    Import-Module -Name $sdk -ErrorAction Stop

    # Invoke-CreateToken targets whichever SDDC Manager the SDK session is bound to, so the
    # operator connects with the SDK's own connect cmdlet before calling us. We only get to
    # name the expected server in diagnostics.
    $spec = Initialize-VcfTokenCreationSpec -Username $Credential.UserName `
                                            -Password $Credential.GetNetworkCredential().Password
    $pair = Invoke-CreateToken -VcfTokenCreationSpec $spec

    if ([string]::IsNullOrWhiteSpace([string] $pair.accessToken)) {
        throw "POST /v1/tokens on $SddcManagerServer returned no accessToken."
    }
    [string] $pair.accessToken
}
