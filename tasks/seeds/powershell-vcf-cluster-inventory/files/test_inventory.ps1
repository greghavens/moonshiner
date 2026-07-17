# Acceptance harness for VcfInventory.psm1.
# Run from the workspace root:  pwsh -NoProfile -File test_inventory.ps1
# Drives the module against a loopback fake SDDC Manager (mock_sddc.py).
# Protected — do not modify this file, mock_sddc.py, or anything under docs/.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$moduleFile = Join-Path $PSScriptRoot 'VcfInventory.psm1'
if (-not (Test-Path -LiteralPath $moduleFile -PathType Leaf)) {
    Write-Output 'FAIL VcfInventory.psm1 not found in the workspace root'
    exit 1
}

$USERNAME = 'svc-inventory'
$PASSWORD = 'dummy-pass-77c1e0'   # dummy; never a real credential
$REFRESH_ID = 'rt-55aa-4b1c-dummy'
$D_MGMT = 'd0a2c9f4-1b2e-4c5d-9a01-3f6b8e7c5a10'
$D_VI = 'b7e41d80-6f2c-49a3-8d15-c09e2a4f7b22'
$C_M01 = 'c66f2b8e-04d1-4a3b-9c77-e5a8f1d20b94'

$T = Join-Path $PSScriptRoot '_t'
$script:checks = 0
$script:fails = 0

function Assert-True {
    param([string]$Label, [bool]$Condition)
    $script:checks++
    if ($Condition) { return }
    $script:fails++
    Write-Output "FAIL $Label"
}

function Assert-Eq {
    param([string]$Label, $Expected, $Actual)
    $script:checks++
    if ("$Expected" -ceq "$Actual") { return }
    $script:fails++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

$srv = $null
try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $portFile = Join-Path $T 'port.txt'
    Remove-Item -LiteralPath $portFile -ErrorAction SilentlyContinue
    $srv = Start-Process -FilePath 'python3' `
        -ArgumentList @((Join-Path $PSScriptRoot 'mock_sddc.py'), $portFile) `
        -PassThru `
        -RedirectStandardOutput (Join-Path $T 'srv.out') `
        -RedirectStandardError (Join-Path $T 'srv.err')
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile)) {
        if ($srv.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            throw "mock server failed to start: $(Get-Content -LiteralPath (Join-Path $T 'srv.err') -Raw -ErrorAction SilentlyContinue)"
        }
        Start-Sleep -Milliseconds 50
    }
    $port = [int](Get-Content -LiteralPath $portFile -Raw).Trim()
    $baseUrl = "http://127.0.0.1:$port"

    function Get-MockLog {
        # Invoke-RestMethod hands a JSON array back as one Object[]; enumerate it
        (Invoke-RestMethod -Uri "$baseUrl/__log__" -Method Get) | ForEach-Object { $_ }
    }
    function Reset-MockLog {
        Invoke-RestMethod -Uri "$baseUrl/__reset_log__" -Method Post > $null
    }
    function Invoke-Control {
        param([string]$Path, [string]$Body = '{}')
        Invoke-RestMethod -Uri "$baseUrl$Path" -Method Post -Body $Body -ContentType 'application/json' > $null
    }

    Import-Module $moduleFile -Force

    # ---- protected docs fixtures must stay valid JSON -----------------------
    foreach ($doc in @('docs/contract.json', 'docs/official_sources.json')) {
        $null = Get-Content -LiteralPath (Join-Path $PSScriptRoot $doc) -Raw | ConvertFrom-Json
        Assert-True "$doc parses" $true
    }

    # ---- connect: documented token pair creation ---------------------------
    $session = Connect-VcfSddcManager -BaseUrl $baseUrl -Username $USERNAME -Password $PASSWORD
    Assert-True 'session carries an access token' (-not [string]::IsNullOrEmpty($session.AccessToken))
    Assert-Eq 'session preserves the refresh token id' $REFRESH_ID $session.RefreshTokenId
    $log = @(Get-MockLog)
    $tokenPosts = @($log | Where-Object { $_.method -eq 'POST' -and $_.path -eq '/v1/tokens' })
    Assert-Eq 'exactly one token POST for connect' 1 $tokenPosts.Count
    Assert-True 'token POST content-type is application/json' ($tokenPosts[0].ctype -like 'application/json*')
    $spec = $tokenPosts[0].body | ConvertFrom-Json
    Assert-Eq 'TokenCreationSpec.username' $USERNAME $spec.username
    Assert-Eq 'TokenCreationSpec.password' $PASSWORD $spec.password
    Assert-Eq 'TokenCreationSpec has exactly two keys' 'password,username' `
        (($spec.PSObject.Properties.Name | Sort-Object) -join ',')

    # ---- bad credentials never leak -----------------------------------------
    $wrongPass = 'dummy-wrong-90bd12'
    $caught = $null
    try {
        Connect-VcfSddcManager -BaseUrl $baseUrl -Username $USERNAME -Password $wrongPass > $null
    } catch {
        $caught = $_
    }
    Assert-True 'wrong password throws' ($null -ne $caught)
    Assert-Eq 'wrong password throws VcfAuthError' 'VcfAuthError' $caught.Exception.GetType().Name
    Assert-Eq 'failed login carries the documented 400' 400 $caught.Exception.StatusCode
    Assert-True 'failed-login message must not contain the password' `
        (-not ("$($caught.Exception.Message)".Contains($wrongPass)))

    # ---- domain enumeration with filters and pagination -----------------------
    Reset-MockLog
    $domains = @(Get-VcfDomains -Session $session -PageSize 1)
    Assert-Eq 'both domains enumerated across pages' 2 $domains.Count
    Assert-Eq 'domain ids preserved verbatim' ($D_MGMT + ',' + $D_VI) `
        ((@($domains | Sort-Object -Property name) | ForEach-Object { $_.id }) -join ',')
    $log = @(Get-MockLog)
    $domainGets = @($log | Where-Object { $_.method -eq 'GET' -and $_.path -eq '/v1/domains' })
    Assert-Eq 'two page requests for pageSize 1' 2 $domainGets.Count
    Assert-Eq 'first page asks pageNumber 0' '0' $domainGets[0].query.pageNumber
    Assert-Eq 'second page asks pageNumber 1' '1' $domainGets[1].query.pageNumber
    Assert-Eq 'pageSize passed through on every page' '1,1' `
        (($domainGets | ForEach-Object { $_.query.pageSize }) -join ',')
    Assert-True 'domain requests carry the bearer token' `
        ($domainGets[0].auth -ceq ('Bearer ' + $session.AccessToken))

    Reset-MockLog
    $mgmt = @(Get-VcfDomains -Session $session -Type 'MANAGEMENT' -PageSize 50)
    Assert-Eq 'type filter returns only the management domain' 1 $mgmt.Count
    Assert-Eq 'management domain name' 'sfo-m01' $mgmt[0].name
    $log = @(Get-MockLog)
    Assert-Eq 'type filter encoded on the wire' 'MANAGEMENT' $log[0].query.type

    # ---- cluster enumeration ---------------------------------------------------
    Reset-MockLog
    $viClusters = @(Get-VcfClusters -Session $session -DomainId $D_VI -PageSize 1)
    Assert-Eq 'both VI clusters enumerated across pages' 2 $viClusters.Count
    Assert-Eq 'cluster names' 'sfo-w01-cl01,sfo-w01-cl02' `
        ((@($viClusters | Sort-Object -Property name) | ForEach-Object { $_.name }) -join ',')
    $log = @(Get-MockLog)
    $clusterGets = @($log | Where-Object { $_.method -eq 'GET' -and $_.path -eq '/v1/clusters' })
    Assert-Eq 'domainId filter on every cluster page' ($D_VI + ',' + $D_VI) `
        (($clusterGets | ForEach-Object { $_.query.domainId }) -join ',')

    Reset-MockLog
    $stretched = @(Get-VcfClusters -Session $session -DomainId $D_VI -IsStretched $true -PageSize 50)
    Assert-Eq 'isStretched filter returns one cluster' 1 $stretched.Count
    Assert-Eq 'stretched cluster is sfo-w01-cl01' 'sfo-w01-cl01' $stretched[0].name
    $log = @(Get-MockLog)
    Assert-Eq 'isStretched encoded lowercase' 'true' $log[0].query.isStretched

    # ---- datastore subresource ---------------------------------------------------
    $ds = @(Get-VcfClusterDatastores -Session $session -ClusterId $C_M01)
    Assert-Eq 'management cluster has two datastores' 2 $ds.Count
    Assert-Eq 'datastore ids preserved' 'ds-29a4f6d1,ds-71c0e8b5' `
        ((@($ds | Sort-Object -Property id) | ForEach-Object { $_.id }) -join ',')

    # ---- 401: refresh once, retry once -----------------------------------------
    Invoke-Control '/__expire__'
    Reset-MockLog
    $before = $session.AccessToken
    $domains = @(Get-VcfDomains -Session $session -PageSize 50)
    Assert-Eq 'expired token recovered transparently' 2 $domains.Count
    $log = @(Get-MockLog)
    $patches = @($log | Where-Object { $_.method -eq 'PATCH' -and $_.path -eq '/v1/tokens/access-token/refresh' })
    Assert-Eq 'exactly one refresh PATCH' 1 $patches.Count
    Assert-Eq 'refresh body is the refresh id as a bare JSON string' ('"' + $REFRESH_ID + '"') $patches[0].body
    Assert-True 'refresh content-type is application/json' ($patches[0].ctype -like 'application/json*')
    $domainGets = @($log | Where-Object { $_.method -eq 'GET' -and $_.path -eq '/v1/domains' })
    Assert-Eq 'the 401d request is retried exactly once' 2 $domainGets.Count
    Assert-True 'session now carries a rotated token' ($session.AccessToken -cne $before)
    Assert-True 'retry used the rotated token' `
        ($domainGets[1].auth -ceq ('Bearer ' + $session.AccessToken))

    # ---- unrecoverable 401 -------------------------------------------------------
    Invoke-Control '/__expire__'
    Invoke-Control '/__revoke_refresh__'
    $caught = $null
    try {
        Get-VcfDomains -Session $session -PageSize 50 > $null
    } catch {
        $caught = $_
    }
    Assert-True 'revoked refresh throws' ($null -ne $caught)
    Assert-Eq 'revoked refresh throws VcfAuthError' 'VcfAuthError' $caught.Exception.GetType().Name
    Invoke-Control '/__restore_refresh__'

    # ---- 403 is terminal and refresh-free -----------------------------------------
    Get-VcfDomains -Session $session -PageSize 50 > $null   # re-establish a valid token
    Invoke-Control '/__mode__' '{"forbidden": true}'
    Reset-MockLog
    $caught = $null
    try {
        Get-VcfClusters -Session $session -DomainId $D_MGMT -PageSize 50 > $null
    } catch {
        $caught = $_
    }
    Invoke-Control '/__mode__' '{}'
    Assert-True '403 throws' ($null -ne $caught)
    Assert-Eq '403 throws VcfForbiddenError' 'VcfForbiddenError' $caught.Exception.GetType().Name
    Assert-Eq '403 carries StatusCode' 403 $caught.Exception.StatusCode
    Assert-Eq '403 carries ErrorCode' 'FORBIDDEN' $caught.Exception.ErrorCode
    Assert-Eq '403 carries ReferenceToken' 'F0RB1D' $caught.Exception.ReferenceToken
    $log = @(Get-MockLog)
    $patches = @($log | Where-Object { $_.method -eq 'PATCH' })
    Assert-Eq '403 triggers zero token refreshes' 0 $patches.Count

    # ---- 5xx --------------------------------------------------------------------
    Invoke-Control '/__mode__' '{"fail": true}'
    $caught = $null
    try {
        Get-VcfDomains -Session $session -PageSize 50 > $null
    } catch {
        $caught = $_
    }
    Invoke-Control '/__mode__' '{}'
    Assert-True '500 throws' ($null -ne $caught)
    Assert-Eq '500 throws VcfServerError' 'VcfServerError' $caught.Exception.GetType().Name
    Assert-Eq '500 carries StatusCode' 500 $caught.Exception.StatusCode
    Assert-Eq '500 carries ErrorCode' 'VCF_SYSTEM_ERROR' $caught.Exception.ErrorCode
    Assert-Eq '500 carries ReferenceToken' 'SRV5XX' $caught.Exception.ReferenceToken

    # ---- stable JSON export --------------------------------------------------------
    $out1 = Join-Path $T 'inventory1.json'
    $out2 = Join-Path $T 'inventory2.json'
    Export-VcfClusterInventory -Session $session -Path $out1 -PageSize 1
    Export-VcfClusterInventory -Session $session -Path $out2 -PageSize 1
    $raw1 = Get-Content -LiteralPath $out1 -Raw
    $raw2 = Get-Content -LiteralPath $out2 -Raw
    Assert-True 'two exports are byte-identical despite scrambled API order' ($raw1 -ceq $raw2)

    $inv = $raw1 | ConvertFrom-Json
    Assert-Eq 'root has exactly the domains key' 'domains' `
        (($inv.PSObject.Properties.Name) -join ',')
    $doms = @($inv.domains)
    Assert-Eq 'export contains both domains' 2 $doms.Count
    Assert-Eq 'domains sorted by name' 'sfo-m01,sfo-w01' (($doms | ForEach-Object { $_.name }) -join ',')
    Assert-Eq 'domain key order' 'id,name,type,status,clusters' `
        (($doms[0].PSObject.Properties.Name) -join ',')
    Assert-Eq 'management domain id verbatim' $D_MGMT $doms[0].id
    Assert-Eq 'VI domain id verbatim' $D_VI $doms[1].id

    $mgmtClusters = @($doms[0].clusters)
    $viClusters = @($doms[1].clusters)
    Assert-Eq 'management domain has one cluster' 1 $mgmtClusters.Count
    Assert-Eq 'VI domain clusters sorted by name' 'sfo-w01-cl01,sfo-w01-cl02' `
        (($viClusters | ForEach-Object { $_.name }) -join ',')
    $cl = $mgmtClusters[0]
    Assert-Eq 'cluster key order' 'id,name,status,isDefault,isStretched,primaryDatastoreName,primaryDatastoreType,hosts,datastores' `
        (($cl.PSObject.Properties.Name) -join ',')
    Assert-Eq 'cluster id verbatim' $C_M01 $cl.id
    Assert-Eq 'primary datastore name' 'sfo-m01-cl01-ds-vsan01' $cl.primaryDatastoreName
    Assert-Eq 'primary datastore type' 'VSAN' $cl.primaryDatastoreType
    Assert-True 'isDefault preserved as boolean true' ($cl.isDefault -is [bool] -and $cl.isDefault)
    Assert-True 'isStretched preserved as boolean false' ($cl.isStretched -is [bool] -and -not $cl.isStretched)
    Assert-True 'stretched cluster keeps boolean true' `
        ($viClusters[0].isStretched -is [bool] -and $viClusters[0].isStretched)

    $hosts = @($cl.hosts)
    Assert-Eq 'hosts sorted by fqdn' 'esx01.sfo.rainpole.io,esx02.sfo.rainpole.io,esx03.sfo.rainpole.io' `
        (($hosts | ForEach-Object { $_.fqdn }) -join ',')
    Assert-Eq 'host key order' 'id,fqdn,ipAddress,azName' (($hosts[0].PSObject.Properties.Name) -join ',')
    Assert-Eq 'host id verbatim' '1f4b8d20-93c6-47a1-b5e9-62d0a8c4f715' $hosts[0].id
    Assert-Eq 'host ipAddress preserved' '10.0.10.101' $hosts[0].ipAddress
    Assert-Eq 'expanding cluster status preserved' 'EXPANDING' $viClusters[1].status

    $dsList = @($cl.datastores)
    Assert-Eq 'datastores sorted by name' 'sfo-m01-cl01-ds-nfs01,sfo-m01-cl01-ds-vsan01' `
        (($dsList | ForEach-Object { $_.name }) -join ',')
    Assert-Eq 'datastore key order' 'id,name,datastoreType,totalCapacityGB,freeCapacityGB,vmCount' `
        (($dsList[0].PSObject.Properties.Name) -join ',')
    Assert-Eq 'datastore capacity preserved' 18432 $dsList[1].totalCapacityGB
    Assert-Eq 'datastore free space preserved' 9016.25 $dsList[1].freeCapacityGB
    Assert-Eq 'datastore vm count preserved' 37 $dsList[1].vmCount
    Assert-Eq 'VI cluster datastore id verbatim' 'ds-5b3e0c97' (@($viClusters[0].datastores))[0].id
} finally {
    if ($null -ne $srv -and -not $srv.HasExited) {
        Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "checks=$($script:checks) fails=$($script:fails)"
if ($script:fails -gt 0) { exit 1 }
Write-Output 'ALL TESTS PASSED'
exit 0
