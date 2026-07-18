function ConvertFrom-RouteProbe {
    [CmdletBinding()]
    param([Parameter(Mandatory)][object]$InputObject)

    $computer = [string]$InputObject.ComputerName
    foreach ($route in @($InputObject.Routes)) {
        $rawAddress = [string]$route.DestinationAddress
        $address = $null
        if (-not [System.Net.IPAddress]::TryParse($rawAddress, [ref]$address) -or
            $address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
            throw "route from ${computer}: bad IPv4 address '$rawAddress'"
        }

        $prefix = 0
        $rawPrefix = [string]$route.PrefixLength
        if (-not [int]::TryParse($rawPrefix, [ref]$prefix) -or $prefix -lt 0 -or $prefix -gt 32) {
            throw "route from ${computer}: bad prefix '$rawPrefix'"
        }

        $metric = 0
        $rawMetric = [string]$route.Metric
        if (-not [int]::TryParse($rawMetric, [ref]$metric) -or $metric -lt 0) {
            throw "route from ${computer}: bad metric '$rawMetric'"
        }

        [pscustomobject]@{
            ComputerName = $computer
            Destination  = "$address/$prefix"
            NextHop      = [string]$route.NextHop
            Metric       = [int]$metric
        }
    }
}
