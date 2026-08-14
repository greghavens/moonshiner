Set-StrictMode -Version Latest

function Sync-VcfAutomationProject {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $RefreshToken,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object[]] $Project
    )

    throw [System.NotImplementedException]::new('Sync-VcfAutomationProject is not implemented.')
}

Export-ModuleMember -Function Sync-VcfAutomationProject
