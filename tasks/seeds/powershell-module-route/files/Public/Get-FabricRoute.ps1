function Get-FabricRoute {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, ValueFromPipeline)]
        [ValidateNotNull()]
        [object]$Session
    )

    process {
        $probe = Invoke-RouteProbe -Session $Session
        ConvertFrom-RouteProbe -InputObject $probe
    }
}
