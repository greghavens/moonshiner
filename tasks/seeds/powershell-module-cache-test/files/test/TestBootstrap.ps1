function Import-ModuleUnderTest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $Path,

        [Parameter(Mandatory)]
        [string] $Name
    )

    Import-Module -Name $Path -Global -ErrorAction Stop
}
