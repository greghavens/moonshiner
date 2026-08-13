<#
.SYNOPSIS
    Runs Get-VcfOpsResourceInventory against a loopback mock of the VCF Operations
    suite-api and asserts both the snapshot it produced and the exact shape of
    every request it sent.

.DESCRIPTION
    No VMware endpoint is contacted. The only socket opened is a 127.0.0.1
    listener served by tools/mock/Start-VcfOpsMock.ps1, which answers only the
    operations docs/contract.json names.

    Three scenarios run:

      fleet     -- no filters at all. Five pages, one of them short, two resources
                   repeated across page boundaries, and a pageInfo.totalCount that
                   under-reports. The query string must carry nothing but page and
                   pageSize.

      filtered  -- adapter, resource, health, timestamp, and property filters
                   supplied. Three pages, one repeat, and a pageInfo.totalCount
                   that over-reports.

      boundary  -- an empty terminal collection with a two-value Name filter,
                   CreatedAfter 0, and explicitly bound empty property filters.

    Exits 0 when every assertion holds, 1 otherwise.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'tools/mock/Start-VcfOpsMock.ps1')

$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Checks = 0

function Assert-That {
    param([Parameter(Mandatory)] [string] $What, [Parameter(Mandatory)] [bool] $Condition, [string] $Detail)
    $script:Checks++
    if ($Condition) { return }
    $message = $What
    if ($Detail) { $message = "$What`n      $Detail" }
    $script:Failures.Add($message)
    Write-Host "  FAIL  $message" -ForegroundColor Red
}

function Assert-Equal {
    param([Parameter(Mandatory)] [string] $What, $Expected, $Actual)
    Assert-That -What $What -Condition ("$Expected" -ceq "$Actual") -Detail "expected: $Expected`n      actual:   $Actual"
}

# --------------------------------------------------------------- expectations

$expected = @{
    fleet    = @{
        Fixture            = 'scenario-fleet.json'
        PageSize           = 5
        Pages              = 5
        DuplicatesDropped  = 2
        ReportedTotalCount = 15
        Token              = 'ops-token-fleet-8f2c41'
        # No filters supplied, so nothing but the paging parameters may appear.
        QueryPrefix        = ''
        SuppliedQueryNames = @()
        AllowedEmptyNames  = @()
        Resources          = @(
            @{ Identifier = 'b67d8292-c759-5934-895b-eb357c80847d'; Name = 'App-Tier-1'; AdapterKind = 'Container'; ResourceKind = 'Tier'; Health = 'GREEN'; CreationTime = 1767226320000 }
            @{ Identifier = 'b5a3d627-3c42-5cf6-8c38-de7318ef0258'; Name = 'ESX-02.dc1.example.com'; AdapterKind = 'VMWARE'; ResourceKind = 'HostSystem'; Health = 'YELLOW'; CreationTime = 1767225660000 }
            @{ Identifier = '654974ea-3882-5eb0-8807-cd86090b83a1'; Name = 'NSX-Edge-01'; AdapterKind = 'NSXT'; ResourceKind = 'TransportNode'; Health = 'GREEN'; CreationTime = 1767226500000 }
            @{ Identifier = '21ba2937-f6c9-53db-959f-28c64faaa76e'; Name = 'VC-01.dc2.example.com'; AdapterKind = 'VMWARE'; ResourceKind = 'VirtualMachine'; Health = 'GREEN'; CreationTime = 1767226020000 }
            @{ Identifier = 'b19a67f1-8615-5f41-a936-f1c542b2de05'; Name = 'app-tier-10'; AdapterKind = 'Container'; ResourceKind = 'Tier'; Health = 'GREEN'; CreationTime = 1767226380000 }
            @{ Identifier = '0e786f34-5c5a-5d73-a5ea-5cc859ad9eb4'; Name = 'app-tier-2'; AdapterKind = 'Container'; ResourceKind = 'Tier'; Health = 'YELLOW'; CreationTime = 1767226440000 }
            @{ Identifier = '535c7631-a5a0-5f41-9c41-f4bf9d592d03'; Name = 'ds-local-01'; AdapterKind = 'VMWARE'; ResourceKind = 'Datastore'; Health = 'GREEN'; CreationTime = 1767226200000 }
            @{ Identifier = '1aea055e-98d5-5317-8369-dadf595959a4'; Name = 'ds_local-01'; AdapterKind = 'VMWARE'; ResourceKind = 'Datastore'; Health = 'GREEN'; CreationTime = 1767226260000 }
            @{ Identifier = '9b8c0ac2-23da-56c9-9711-099c80c88990'; Name = 'esx-01.dc1.example.com'; AdapterKind = 'VMWARE'; ResourceKind = 'HostSystem'; Health = 'GREEN'; CreationTime = 1767225600000 }
            @{ Identifier = '23251280-308e-55be-a90a-dd2c1c3c7ff4'; Name = 'esx-10.dc1.example.com'; AdapterKind = 'VMWARE'; ResourceKind = 'HostSystem'; Health = 'GREEN'; CreationTime = 1767225780000 }
            @{ Identifier = '78750c5d-ca87-55cf-8b06-fb97cc39443e'; Name = 'esx-2.dc1.example.com'; AdapterKind = 'VMWARE'; ResourceKind = 'HostSystem'; Health = 'RED'; CreationTime = 1767225840000 }
            @{ Identifier = 'e7012639-2d8b-57cb-8dac-068c169b9a7f'; Name = 'esx_03.dc1.example.com'; AdapterKind = 'VMWARE'; ResourceKind = 'HostSystem'; Health = 'GREEN'; CreationTime = 1767225720000 }
            @{ Identifier = '3b6fa1f7-8e00-5410-9335-08d20f9fe86a'; Name = 'nsx-edge-01'; AdapterKind = 'NSXT'; ResourceKind = 'TransportNode'; Health = 'GREEN'; CreationTime = 1767226560000 }
            @{ Identifier = 'c53ce53b-4c5b-5d27-ba98-d102137d0507'; Name = 'vc-01.dc1.example.com'; AdapterKind = 'VMWARE'; ResourceKind = 'VirtualMachine'; Health = 'GREEN'; CreationTime = 1767225900000 }
            @{ Identifier = 'e3b60091-11cb-5974-a082-0ca09a061a23'; Name = 'vc-01.dc1.example.com'; AdapterKind = 'VMWARE'; ResourceKind = 'VMwareAdapter Instance'; Health = 'GREEN'; CreationTime = 1767225960000 }
            @{ Identifier = 'af3ce1f0-7417-5ad6-a9f2-5c43b2da46d0'; Name = 'vsan-cluster-A'; AdapterKind = 'VMWARE'; ResourceKind = 'VSANCluster'; Health = 'GREEN'; CreationTime = 1767226080000 }
            @{ Identifier = 'a1d040e5-a7e5-528d-ac22-c15446a8e3f1'; Name = 'vsan-cluster-a'; AdapterKind = 'VMWARE'; ResourceKind = 'VSANCluster'; Health = 'YELLOW'; CreationTime = 1767226140000 }
            @{ Identifier = '02289e31-f5d2-5612-a24e-333ea1ed0371'; Name = 'web-01'; AdapterKind = 'VMWARE'; ResourceKind = 'VirtualMachine'; Health = 'GREEN'; CreationTime = 1767226620000 }
        )
    }
    filtered = @{
        Fixture            = 'scenario-filtered.json'
        PageSize           = 4
        Pages              = 3
        DuplicatesDropped  = 1
        ReportedTotalCount = 12
        Token              = 'ops-token-filtered-3ad907'
        # Specification declaration order, with page and pageSize last.
        QueryPrefix        = 'adapterKind=VMWARE&resourceKind=HostSystem&resourceKind=VirtualMachine&recentlyAdded=1767225600&resourceHealth=GREEN&resourceHealth=YELLOW&propertyName=summary%7Ctag&propertyValue=prod&'
        SuppliedQueryNames = @('adapterKind', 'resourceKind', 'recentlyAdded', 'resourceHealth', 'propertyName', 'propertyValue')
        AllowedEmptyNames  = @()
        Arguments          = @{
            AdapterKind    = 'VMWARE'
            ResourceKind   = 'HostSystem,VirtualMachine'
            ResourceHealth = 'GREEN,YELLOW'
            CreatedAfter   = 1767225600
            PropertyName   = 'summary|tag'
            PropertyValue  = 'prod'
        }
        Resources          = @(
            @{ Identifier = 'c2935070-fe16-5138-9034-48d71f3e1428'; Name = 'Cluster-Prod-01'; AdapterKind = 'VMWARE'; ResourceKind = 'HostSystem'; Health = 'GREEN'; CreationTime = 1767226500000 }
            @{ Identifier = '9492865d-3a7f-5b45-bbd6-c776ca8e019d'; Name = 'Edge_Node-A'; AdapterKind = 'VMWARE'; ResourceKind = 'VirtualMachine'; Health = 'GREEN'; CreationTime = 1767226740000 }
            @{ Identifier = '4e5914c1-602d-5720-a2c2-9333ee15ed90'; Name = 'RP-Shared'; AdapterKind = 'VMWARE'; ResourceKind = 'VirtualMachine'; Health = 'GREEN'; CreationTime = 1767226920000 }
            @{ Identifier = '1fbd5a43-e053-5ae4-b133-046ab8791acb'; Name = 'cluster-prod-01'; AdapterKind = 'VMWARE'; ResourceKind = 'HostSystem'; Health = 'YELLOW'; CreationTime = 1767226560000 }
            @{ Identifier = '0021d9ab-f13e-57b4-b0cb-c891e1388a66'; Name = 'cluster-prod-10'; AdapterKind = 'VMWARE'; ResourceKind = 'HostSystem'; Health = 'GREEN'; CreationTime = 1767226620000 }
            @{ Identifier = '1f69ba7e-bf4e-570a-a465-24d6eef8451a'; Name = 'cluster-prod-2'; AdapterKind = 'VMWARE'; ResourceKind = 'HostSystem'; Health = 'GREEN'; CreationTime = 1767226680000 }
            @{ Identifier = 'ae1f8807-734c-5904-a036-788bd74ddcff'; Name = 'edge-node-a'; AdapterKind = 'VMWARE'; ResourceKind = 'VirtualMachine'; Health = 'YELLOW'; CreationTime = 1767226800000 }
            @{ Identifier = '0c49babe-ff0d-533c-bb24-e3130b346bec'; Name = 'rp-shared'; AdapterKind = 'VMWARE'; ResourceKind = 'VirtualMachine'; Health = 'GREEN'; CreationTime = 1767226860000 }
            @{ Identifier = '45bd89a7-588e-5715-a52f-e7fa2e1451d4'; Name = 'zz-tail-01'; AdapterKind = 'VMWARE'; ResourceKind = 'VirtualMachine'; Health = 'YELLOW'; CreationTime = 1767226980000 }
        )
    }
    boundary = @{
        Fixture            = 'scenario-boundary.json'
        PageSize           = 3
        Pages              = 1
        DuplicatesDropped  = 0
        ReportedTotalCount = 0
        Token              = 'ops-token-boundary-71c5e2'
        QueryPrefix        = 'name=zero%7Cname&name=zero_name&recentlyAdded=0&propertyName=&propertyValue=&'
        SuppliedQueryNames = @('name', 'recentlyAdded', 'propertyName', 'propertyValue')
        AllowedEmptyNames  = @('propertyName', 'propertyValue')
        Arguments          = @{
            Name                   = 'zero|name,zero_name'
            CreatedAfter           = 0
            BindEmptyStringFilters = $true
        }
        Resources          = @()
    }
}

$contractOperationIds = @('acquireToken', 'getCurrentVersionOfServer', 'getResources')

# ------------------------------------------------------------------ the runs

$modulePath = Join-Path $repoRoot 'src/VcfOpsInventory/VcfOpsInventory.psd1'
$pwshPath = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vcf-ops-inventory-" + [guid]::NewGuid().ToString('n'))
$null = New-Item -ItemType Directory -Path $workDir

Write-Host 'Contract' -ForegroundColor Cyan
$contract = Get-Content -LiteralPath (Join-Path $repoRoot 'docs/contract.json') -Raw | ConvertFrom-Json
$declared = @($contract.operations.PSObject.Properties.Name | Sort-Object)
Assert-Equal -What 'docs/contract.json names the operations the mock serves' `
    -Expected ($contractOperationIds -join ',') -Actual ($declared -join ',')

try {
    foreach ($scenario in @('fleet', 'filtered', 'boundary')) {
        $case = $expected[$scenario]
        Write-Host ''
        Write-Host "Scenario: $scenario" -ForegroundColor Cyan

        $logPath = Join-Path $workDir "$scenario.requests.jsonl"
        $boundaryPath = Join-Path $workDir "$scenario.boundary"
        $resultPath = Join-Path $workDir "$scenario.result.json"
        $stdoutPath = Join-Path $workDir "$scenario.stdout"
        $stderrPath = Join-Path $workDir "$scenario.stderr"

        $mock = Start-VcfOpsMock `
            -FixturePath (Join-Path $repoRoot "tools/mock/fixtures/$($case.Fixture)") `
            -LogPath $logPath

        try {
            $arguments = @(
                '-NoProfile', '-File', (Join-Path $repoRoot 'tests/harness/Invoke-InventoryRun.ps1'),
                '-Port', $mock.Port,
                '-ModulePath', $modulePath,
                '-LogPath', $logPath,
                '-BoundaryPath', $boundaryPath,
                '-ResultPath', $resultPath,
                '-PageSize', $case.PageSize
            )
            if ($case.ContainsKey('Arguments')) {
                foreach ($key in @('Name', 'AdapterKind', 'ResourceKind', 'ResourceHealth', 'CreatedAfter', 'PropertyName', 'PropertyValue', 'BindEmptyStringFilters')) {
                    if (-not $case.Arguments.ContainsKey($key)) { continue }
                    if ($case.Arguments[$key] -is [bool]) {
                        if ($case.Arguments[$key]) { $arguments += "-$key" }
                    }
                    else {
                        $arguments += @("-$key", $case.Arguments[$key])
                    }
                }
            }

            $process = Start-Process -FilePath $pwshPath -ArgumentList $arguments -PassThru -NoNewWindow `
                -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
            if (-not $process.WaitForExit(240000)) {
                try { $process.Kill($true) } catch { }
                Assert-That -What 'the run finished' -Condition $false `
                    -Detail 'Get-VcfOpsResourceInventory did not return within 240s -- it is most likely paging without a termination condition'
                continue
            }

            $stderr = if (Test-Path -LiteralPath $stderrPath) { (Get-Content -LiteralPath $stderrPath -Raw) } else { '' }
            Assert-That -What 'the run exited 0' -Condition ($process.ExitCode -eq 0) `
                -Detail "exit code $($process.ExitCode)`n      $($stderr -replace "`n", "`n      ")"
            if ($process.ExitCode -ne 0) { continue }
        }
        finally {
            Stop-VcfOpsMock -Mock $mock
        }

        # ------------------------------------------------------ the snapshot

        $snapshot = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
        $returned = @($snapshot.Resources)

        Assert-Equal -What 'ResourceCount' -Expected $case.Resources.Count -Actual $snapshot.ResourceCount
        Assert-Equal -What 'PagesFetched' -Expected $case.Pages -Actual $snapshot.PagesFetched
        Assert-Equal -What 'DuplicatesDropped' -Expected $case.DuplicatesDropped -Actual $snapshot.DuplicatesDropped
        Assert-Equal -What 'ReportedTotalCount is passed through, not used as the stopping rule' `
            -Expected $case.ReportedTotalCount -Actual $snapshot.ReportedTotalCount
        Assert-Equal -What 'every resource is emitted exactly once' -Expected $case.Resources.Count -Actual $returned.Count

        if ($returned.Count -eq $case.Resources.Count) {
            $orderOk = $true
            for ($i = 0; $i -lt $case.Resources.Count; $i++) {
                $want = $case.Resources[$i]
                $got = $returned[$i]
                if (($got.Identifier -cne $want.Identifier) -or ($got.Name -cne $want.Name)) {
                    Assert-That -What "resource at index $i" -Condition $false `
                        -Detail ("expected: {0}  {1}`n      actual:   {2}  {3}" -f $want.Name, $want.Identifier, $got.Name, $got.Identifier)
                    $orderOk = $false
                }
            }
            if ($orderOk) {
                Write-Host "  ok    all $($returned.Count) resources present, in ordinal order by name then identifier"
                $script:Checks++
                for ($i = 0; $i -lt $case.Resources.Count; $i++) {
                    $want = $case.Resources[$i]
                    $got = $returned[$i]
                    Assert-Equal -What "resource $($want.Name) AdapterKind" -Expected $want.AdapterKind -Actual $got.AdapterKind
                    Assert-Equal -What "resource $($want.Name) ResourceKind" -Expected $want.ResourceKind -Actual $got.ResourceKind
                    Assert-Equal -What "resource $($want.Name) Health" -Expected $want.Health -Actual $got.Health
                    Assert-Equal -What "resource $($want.Name) CreationTime" -Expected $want.CreationTime -Actual $got.CreationTime
                }
            }
        }

        # ----------------------------------------------------------- the wire

        $all = Get-VcfOpsMockRequests -LogPath $logPath
        $boundary = [int](Get-Content -LiteralPath $boundaryPath -Raw)

        $unknown = @($all | Where-Object { -not $_.served })
        Assert-That -What 'no request landed outside docs/contract.json' -Condition ($unknown.Count -eq 0) `
            -Detail (($unknown | ForEach-Object { "$($_.method) $($_.path) -> $($_.status)" }) -join "`n      ")

        $bootstrap = @($all | Select-Object -First $boundary)
        Assert-Equal -What 'Connect-VcfOpsServer bootstrap' `
            -Expected 'acquireToken,getCurrentVersionOfServer' `
            -Actual (($bootstrap | ForEach-Object { $_.served }) -join ',')

        $moduleRequests = @($all | Select-Object -Skip $boundary)
        Assert-Equal -What 'requests issued by the module' -Expected $case.Pages -Actual $moduleRequests.Count

        if ($moduleRequests.Count -eq $case.Pages) {
            for ($i = 0; $i -lt $case.Pages; $i++) {
                $req = $moduleRequests[$i]
                $label = "request $($i + 1)"
                Assert-Equal -What "$label operation" -Expected 'getResources' -Actual $req.served
                Assert-Equal -What "$label method" -Expected 'GET' -Actual $req.method
                Assert-Equal -What "$label path" -Expected '/suite-api/api/resources' -Actual $req.path
                Assert-Equal -What "$label status" -Expected 200 -Actual $req.status
                Assert-Equal -What "$label Authorization header" -Expected "OpsToken $($case.Token)" -Actual $req.authorization
                Assert-Equal -What "$label carries no body" -Expected '' -Actual $req.body
                Assert-Equal -What "$label query string" `
                    -Expected ("{0}page={1}&pageSize={2}" -f $case.QueryPrefix, $i, $case.PageSize) `
                    -Actual $req.query
            }
        }

        # The omit-when-unset rule, called out on its own so a violation reads as
        # what it is rather than as a query string diff.
        foreach ($req in $moduleRequests) {
            $pairs = @($req.query -split '&' | Where-Object { $_ })
            $empty = @($pairs | Where-Object {
                    $name = ($_ -split '=', 2)[0]
                    ($_ -match '=$' -or $_ -notmatch '=') -and $case.AllowedEmptyNames -notcontains $name
                })
            Assert-That -What "no unset parameter is represented by an empty value ($($req.query))" -Condition ($empty.Count -eq 0) `
                -Detail ($empty -join ', ')

            $names = @($pairs | ForEach-Object { ($_ -split '=', 2)[0] })
            $supplied = @('page', 'pageSize') + @($case.SuppliedQueryNames)
            $unset = @($names | Where-Object { $supplied -notcontains $_ } | Sort-Object -Unique)
            Assert-That -What "no unset optional parameter reaches the wire ($($req.query))" -Condition ($unset.Count -eq 0) `
                -Detail ("sent but never supplied by the caller: " + ($unset -join ', '))
        }
    }
}
finally {
    Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
if ($script:Failures.Count -eq 0) {
    Write-Host "PASS  $($script:Checks) checks" -ForegroundColor Green
    exit 0
}
Write-Host "FAIL  $($script:Failures.Count) of $($script:Checks) checks" -ForegroundColor Red
exit 1
