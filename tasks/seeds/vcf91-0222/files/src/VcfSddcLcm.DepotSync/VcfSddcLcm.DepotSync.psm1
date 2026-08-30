Set-StrictMode -Version Latest

function Invoke-VcfSddcLcmDepotSync {
    [CmdletBinding()]
    param(
        # Caller-owned SDDC LCM access token.
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [securestring] $AccessToken,

        # Returns the replacement SDDC LCM access token after a 401.
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [scriptblock] $RefreshAccessToken,

        # Base URL of the SDDC LCM service, for example http://127.0.0.1:8080
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $LcmBaseUrl,

        # FleetDepotSpec.fqdn
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $DepotFqdn,

        # FleetDepotSpec.certificate
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $DepotCertificate,

        # One entry per component. Each entry has a required 'component' key and an
        # optional 'version' key; an absent or empty 'version' must stay absent on the wire.
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [hashtable[]] $ComponentVersion,

        # Optional DepotComponentsSpec.version. Unset means absent on the wire.
        [Parameter()]
        [AllowNull()]
        [string] $TargetVersion,

        # Optional X-Correlation-Id header for setDepot. Unset means the header is not sent.
        [Parameter()]
        [AllowNull()]
        [string] $CorrelationId
    )

    throw 'Invoke-VcfSddcLcmDepotSync has not been implemented.'
}

Export-ModuleMember -Function Invoke-VcfSddcLcmDepotSync
