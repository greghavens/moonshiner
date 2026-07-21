Set-StrictMode -Version Latest

$script:ProfilesByUserId = @{}

function Invoke-ProfileLookup {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $UserId
    )

    throw "No profile provider is configured for '$UserId'."
}

function Get-CachedProfile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $UserId
    )

    if ($script:ProfilesByUserId.ContainsKey($UserId)) {
        return $script:ProfilesByUserId[$UserId]
    }

    $profile = Invoke-ProfileLookup -UserId $UserId
    $script:ProfilesByUserId[$UserId] = $profile
    return $profile
}

function Clear-ProfileCache {
    [CmdletBinding()]
    param()

    $script:ProfilesByUserId.Clear()
}

Export-ModuleMember -Function Get-CachedProfile, Clear-ProfileCache
