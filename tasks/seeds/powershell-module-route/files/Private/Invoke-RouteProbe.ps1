function Invoke-RouteProbe {
    [CmdletBinding()]
    param([Parameter(Mandatory)][object]$Session)

    $computer = [string]$Session.ComputerName
    if ([string]::IsNullOrWhiteSpace($computer)) {
        throw 'remote session is missing ComputerName'
    }
    if ($Session.Invoke -isnot [scriptblock]) {
        throw "remote session $computer is missing its Invoke adapter"
    }

    $routes = @(& ($Session.Invoke))
    [pscustomobject]@{
        ComputerName = $computer
        Routes       = $routes
    }
}
