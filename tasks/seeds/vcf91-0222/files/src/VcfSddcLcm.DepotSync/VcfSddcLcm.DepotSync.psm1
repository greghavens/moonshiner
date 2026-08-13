Set-StrictMode -Version Latest

function Invoke-VcfSddcLcmDepotSync {
    [CmdletBinding()]
    param(
        # Caller-owned connected VcfSddcManagerServer. Do not connect or disconnect it.
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object] $Server,

        # Credential minted into a token pair through the genuine SDK binding.
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [pscredential] $Credential,

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
