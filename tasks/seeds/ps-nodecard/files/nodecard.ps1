# nodecard.ps1 -- canonical host-record cards for the rack sheets.
#
# Dot-source this file, then:
#
#   Format-NodeCard web01 lyon edge 10.4.2.7
#   Format-NodeCard db01
#
# Card shape:
#
#   <name>.<site>.grid.internal [<role>] ip=<ip>
#
# name and site are normalized to lowercase; the ip suffix only appears
# when an ip was given. Defaults: site 'hq', role 'app'.

Set-StrictMode -Version Latest

function Format-NodeCard {
    # args: <name> [site] [role] [ip] -- classic positional parsing.
    if ($args.Count -lt 1 -or $args.Count -gt 4) {
        throw 'usage: Format-NodeCard <name> [site] [role] [ip]'
    }
    $name = ([string]$args[0]).ToLowerInvariant()
    $site = if ($args.Count -gt 1) { ([string]$args[1]).ToLowerInvariant() } else { 'hq' }
    $role = if ($args.Count -gt 2) { [string]$args[2] } else { 'app' }
    $ip = if ($args.Count -gt 3) { [string]$args[3] } else { '' }
    $card = "$name.$site.grid.internal [$role]"
    if ($ip -ne '') { $card += " ip=$ip" }
    return $card
}

function Format-RackSheet {
    # One card per line for a whole rack, from parsed inventory rows
    # (anything with host/site/role/ip properties, e.g. Import-Csv).
    param([Parameter(Mandatory)][object[]]$Nodes)
    $lines = @()
    foreach ($n in $Nodes) {
        $lines += (Format-NodeCard $n.host $n.site $n.role $n.ip)
    }
    return (($lines -join "`n") + "`n")
}
