Set-StrictMode -Version Latest

function Ensure-VcfNetworkApplication {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Token,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Name
    )

    throw 'Ensure-VcfNetworkApplication is not implemented.'
}

Export-ModuleMember -Function Ensure-VcfNetworkApplication
