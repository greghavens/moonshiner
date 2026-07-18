Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'FabricRoutes.psd1') -Force

$script:Checks = 0
$script:Fails = 0

function Assert-Eq {
    param([string]$Label, [object]$Expected, [object]$Actual)
    $script:Checks++
    if ([object]::Equals($Expected, $Actual)) { return }
    $script:Fails++
    Write-Output "FAIL $Label (expected '$Expected', got '$Actual')"
}

function Assert-True {
    param([string]$Label, [bool]$Condition)
    $script:Checks++
    if ($Condition) { return }
    $script:Fails++
    Write-Output "FAIL $Label"
}

function New-FakeSession {
    param([string]$ComputerName, [object[]]$Routes, [object]$State)
    $payload = $Routes
    $tracker = $State
    $invoke = {
        $tracker.Calls++
        $payload
    }.GetNewClosure()
    [pscustomobject]@{
        ComputerName = $ComputerName
        Invoke       = $invoke
    }
}

try {
    $module = Get-Module FabricRoutes
    $exports = @($module.ExportedFunctions.Keys)
    [Array]::Sort($exports, [System.StringComparer]::Ordinal)
    Assert-Eq 'manifest exports exactly one command' 'Get-FabricRoute' ($exports -join ',')
    Assert-Eq 'module command count' 1 @(Get-Command -Module FabricRoutes).Count
    Assert-True 'private transformer is not exported' ($null -eq (Get-Command ConvertFrom-RouteProbe -ErrorAction SilentlyContinue))
    Assert-True 'private remoting adapter is not exported' ($null -eq (Get-Command Invoke-RouteProbe -ErrorAction SilentlyContinue))

    $sessionParameter = (Get-Command Get-FabricRoute).Parameters['Session']
    $pipelineAttribute = @($sessionParameter.Attributes | Where-Object { $_ -is [System.Management.Automation.ParameterAttribute] })[0]
    Assert-True 'Session accepts pipeline input' $pipelineAttribute.ValueFromPipeline

    $stateA = [pscustomobject]@{ Calls = 0 }
    $stateB = [pscustomobject]@{ Calls = 0 }
    $sessionA = New-FakeSession 'edge-a' @(
        [pscustomobject]@{ DestinationAddress = '10.42.7.91'; PrefixLength = '24'; NextHop = '10.42.7.1'; Metric = '20' },
        [pscustomobject]@{ DestinationAddress = '172.16.5.200'; PrefixLength = '26'; NextHop = '172.16.5.193'; Metric = '7' }
    ) $stateA
    $sessionB = New-FakeSession 'edge-b' @(
        [pscustomobject]@{ DestinationAddress = '192.0.2.11'; PrefixLength = '31'; NextHop = '192.0.2.10'; Metric = '4' },
        [pscustomobject]@{ DestinationAddress = '203.0.113.8'; PrefixLength = '32'; NextHop = '203.0.113.1'; Metric = '0' },
        [pscustomobject]@{ DestinationAddress = '10.8.9.7'; PrefixLength = '0'; NextHop = '10.8.9.1'; Metric = '90' }
    ) $stateB

    $routes = @($sessionA, $sessionB | Get-FabricRoute)
    Assert-Eq 'each session invoked once A' 1 $stateA.Calls
    Assert-Eq 'each session invoked once B' 1 $stateB.Calls
    Assert-Eq 'route count' 5 $routes.Count
    Assert-Eq 'pipeline and remote order preserved' 'edge-a,edge-a,edge-b,edge-b,edge-b' (@($routes | ForEach-Object ComputerName) -join ',')
    Assert-Eq 'network destinations' '10.42.7.0/24,172.16.5.192/26,192.0.2.10/31,203.0.113.8/32,0.0.0.0/0' (@($routes | ForEach-Object Destination) -join ',')
    Assert-Eq 'next hops preserved' '10.42.7.1,172.16.5.193,192.0.2.10,203.0.113.1,10.8.9.1' (@($routes | ForEach-Object NextHop) -join ',')
    Assert-Eq 'property shape preserved' 'ComputerName,Destination,NextHop,Metric' ($routes[0].PSObject.Properties.Name -join ',')
    Assert-True 'metrics are integers' (@($routes | Where-Object { $_.Metric -isnot [int] }).Count -eq 0)
    Assert-Eq 'metrics keep values' '20,7,4,0,90' (@($routes | ForEach-Object Metric) -join ',')

    $envelope = [pscustomobject]@{
        ComputerName = 'direct-edge'
        Routes = @([pscustomobject]@{ DestinationAddress = '198.51.100.143'; PrefixLength = '25'; NextHop = '198.51.100.129'; Metric = '11' })
    }
    $direct = @(& $module { param($Value) ConvertFrom-RouteProbe -InputObject $Value } $envelope)
    Assert-Eq 'private owner canonicalizes direct callers' '198.51.100.128/25' $direct[0].Destination
    Assert-Eq 'private owner types metric' ([int]) $direct[0].Metric.GetType()

    $badAddress = [pscustomobject]@{ ComputerName = 'edge-x'; Routes = @([pscustomobject]@{ DestinationAddress = 'not-an-ip'; PrefixLength = '24'; NextHop = 'x'; Metric = '1' }) }
    $caught = $null
    try { $null = & $module { param($Value) ConvertFrom-RouteProbe -InputObject $Value } $badAddress } catch { $caught = $_.Exception }
    Assert-Eq 'bad address message' "route from edge-x: bad IPv4 address 'not-an-ip'" $caught.Message

    $badPrefix = [pscustomobject]@{ ComputerName = 'edge-y'; Routes = @([pscustomobject]@{ DestinationAddress = '10.0.0.1'; PrefixLength = '33'; NextHop = 'x'; Metric = '1' }) }
    $caught = $null
    try { $null = & $module { param($Value) ConvertFrom-RouteProbe -InputObject $Value } $badPrefix } catch { $caught = $_.Exception }
    Assert-Eq 'bad prefix message' "route from edge-y: bad prefix '33'" $caught.Message

    $throwState = [pscustomobject]@{ Calls = 0 }
    $tracker = $throwState
    $throwing = [pscustomobject]@{
        ComputerName = 'edge-c'
        Invoke = { $tracker.Calls++; throw [InvalidOperationException]::new('remoting offline edge-c') }.GetNewClosure()
    }
    $caught = $null
    try { $null = $throwing | Get-FabricRoute } catch { $caught = $_.Exception }
    Assert-Eq 'remoting failure invoked once' 1 $throwState.Calls
    Assert-Eq 'remoting failure type retained' ([InvalidOperationException]) $caught.GetType()
    Assert-Eq 'remoting failure message retained' 'remoting offline edge-c' $caught.Message
} finally {
    Remove-Module FabricRoutes -ErrorAction SilentlyContinue
}

if ($script:Fails -gt 0) {
    Write-Output "$($script:Fails) of $($script:Checks) checks failed"
    exit 1
}
Write-Output "all checks passed ($($script:Checks) checks)"
exit 0
