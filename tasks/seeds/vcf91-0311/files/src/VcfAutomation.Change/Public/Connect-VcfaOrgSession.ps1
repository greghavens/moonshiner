# SHIPPED - do not modify.
#
# Builds the session object every other function in this module takes. Two ways in:
#
#   -Credential   issue a fresh bearer token through VMware.Sdk.Vcf.SddcManager (VCF PowerCLI 9.1)
#   -AccessToken  reuse a token that was issued elsewhere; no PowerCLI needed
#
function Connect-VcfaOrgSession {
    [CmdletBinding(DefaultParameterSetName = 'Credential')]
    param(
        # VCF Automation appliance base URI, e.g. https://vcfa.rainpole.io
        [Parameter(Mandatory)]
        [string] $BaseUri,

        [Parameter(Mandatory, ParameterSetName = 'Credential')]
        [string] $SddcManagerServer,

        [Parameter(Mandatory, ParameterSetName = 'Credential')]
        [pscredential] $Credential,

        [Parameter(Mandatory, ParameterSetName = 'Token')]
        [string] $AccessToken
    )

    $token = if ($PSCmdlet.ParameterSetName -eq 'Token') {
        $AccessToken
    } else {
        Get-VcfaFleetAccessToken -SddcManagerServer $SddcManagerServer -Credential $Credential
    }

    [pscustomobject] @{
        PSTypeName  = 'Vcfa.OrgSession'
        BaseUri     = $BaseUri.TrimEnd('/')
        AccessToken = $token
    }
}
