# Acceptance tests for the OntapSnapshots module.
# Loopback HttpListener mock speaking the pinned ONTAP REST contract
# (docs/contract.json). No network, no real credentials, injected pacing.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Passed = 0
$script:Failed = 0
function Check([bool]$Condition, [string]$Label) {
    if ($Condition) { $script:Passed++ } else { $script:Failed++; Write-Host "FAIL: $Label" }
}

function Parse-QueryString([string]$RawUrl) {
    $result = @{}
    $idx = $RawUrl.IndexOf('?')
    if ($idx -lt 0) { return $result }
    foreach ($pair in ($RawUrl.Substring($idx + 1) -split '&')) {
        if ($pair -eq '') { continue }
        $kv = $pair -split '=', 2
        $key = [uri]::UnescapeDataString($kv[0])
        $value = if ($kv.Count -gt 1) { [uri]::UnescapeDataString(($kv[1] -replace '\+', ' ')) } else { '' }
        $result[$key] = $value
    }
    return $result
}

$User = 'snap-svc'
$PlainPass = 'dummy-pass-2fa'
$ExpectedAuth = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${User}:${PlainPass}"))
$VolUuid = 'vol-uuid-42'
$SnapBase = "/api/storage/volumes/$VolUuid/snapshots"
$FieldList = @('name', 'create_time', 'expiry_time', 'snapmirror_label', 'size', 'owners')
$FieldsJoined = $FieldList -join ','
$Next1 = "${SnapBase}?start.uuid=snap-0006&fields=$FieldsJoined&max_records=2&order_by=create_time%20asc"
$Next2 = "${SnapBase}?start.uuid=snap-0002&fields=$FieldsJoined&max_records=2&order_by=create_time%20asc"
$JobHref1 = '/api/cluster/jobs/job-del-01?fields=state,message,error'
$JobHref2 = '/api/cluster/jobs/job-del-02?fields=state,message,error'
$JobHref3 = '/api/cluster/jobs/job-del-03?fields=state,message,error'

# ---- module under test (import before the listener starts, so a missing ----
# ---- module fails fast instead of leaving the mock thread alive) -----------

Import-Module (Join-Path $PSScriptRoot 'OntapSnapshots.psm1') -Force

# ---- loopback mock cluster -------------------------------------------------

$State = [hashtable]::Synchronized(@{
    Routes   = [hashtable]::Synchronized(@{})
    Log      = [System.Collections.ArrayList]::Synchronized([System.Collections.ArrayList]::new())
    Ready    = $false
    Listener = $null
})

$tcp = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$tcp.Start()
$Port = ([System.Net.IPEndPoint]$tcp.LocalEndpoint).Port
$tcp.Stop()
$Prefix = "http://127.0.0.1:$Port/"
$BaseUri = "http://127.0.0.1:$Port"

$ServerScript = {
    param($State, $Prefix)
    $listener = [System.Net.HttpListener]::new()
    $listener.Prefixes.Add($Prefix)
    $listener.Start()
    $State.Listener = $listener
    $State.Ready = $true
    while ($true) {
        try { $ctx = $listener.GetContext() } catch { break }
        $req = $ctx.Request
        $null = $State.Log.Add([pscustomobject]@{
            Method = $req.HttpMethod
            Path   = $req.Url.AbsolutePath
            RawUrl = $req.Url.PathAndQuery
            Auth   = $req.Headers['Authorization']
        })
        $key = "$($req.HttpMethod) $($req.Url.AbsolutePath)"
        $resp = $null
        if ($State.Routes.ContainsKey($key)) {
            $queue = $State.Routes[$key]
            if ($queue.Count -gt 0) { $resp = $queue[0]; $queue.RemoveAt(0) }
        }
        if ($null -eq $resp) {
            $resp = @{ Status = 599; Body = '{"error":{"code":"0","message":"UNEXPECTED ' + $key + '"}}' }
        }
        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$resp.Body)
        $ctx.Response.StatusCode = [int]$resp.Status
        $ctx.Response.ContentType = 'application/hal+json'
        $ctx.Response.ContentLength64 = $bytes.Length
        $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        $ctx.Response.Close()
    }
}

$Runspace = [runspacefactory]::CreateRunspace()
$Runspace.Open()
$Server = [powershell]::Create()
$Server.Runspace = $Runspace
$null = $Server.AddScript($ServerScript).AddArgument($State).AddArgument($Prefix)
$ServerHandle = $Server.BeginInvoke()
$spins = 0
while (-not $State.Ready) {
    Start-Sleep -Milliseconds 20
    if (++$spins -gt 250) { throw "mock listener never became ready: $($Server.Streams.Error | Out-String)" }
}

function Reset-Mock([hashtable]$Routes) {
    $State.Routes.Clear()
    foreach ($key in $Routes.Keys) {
        $State.Routes[$key] = [System.Collections.ArrayList]::new(@($Routes[$key]))
    }
    $State.Log.Clear()
}

function Json([object]$Value) { $Value | ConvertTo-Json -Depth 10 -Compress }

function SnapshotPage([object[]]$Records, [string]$NextHref) {
    $links = @{ self = @{ href = $SnapBase } }
    if ($NextHref) { $links.next = @{ href = $NextHref } }
    Json @{ records = $Records; num_records = $Records.Count; _links = $links }
}

$Snap5 = @{ uuid = 'snap-0005'; name = 'manual.2026-07-01_1200'; create_time = '2026-07-01T12:00:00+00:00'; size = 8192 }
$Snap6 = @{ uuid = 'snap-0006'; name = 'adhoc.2026-07-02_0900'; create_time = '2026-07-02T09:00:00+00:00'; expiry_time = '2026-07-05T09:00:00+00:00'; snapmirror_label = 'adhoc'; size = 4096 }
$Snap1 = @{ uuid = 'snap-0001'; name = 'daily.2026-07-10_0010'; create_time = '2026-07-10T00:10:00+00:00'; expiry_time = '2026-07-13T00:10:00+00:00'; snapmirror_label = 'daily'; size = 122880 }
$Snap2 = @{ uuid = 'snap-0002'; name = 'daily.2026-07-11_0010'; create_time = '2026-07-11T00:10:00+00:00'; expiry_time = '2026-07-14T00:10:00+00:00'; snapmirror_label = 'daily'; size = 65536; owners = @() }
$Snap4 = @{ uuid = 'snap-0004'; name = 'daily.2026-07-12_0010'; create_time = '2026-07-12T00:10:00+00:00'; expiry_time = '2026-07-13T00:10:00+00:00'; snapmirror_label = 'daily'; size = 32768; owners = @('snapmirror') }
$Snap3 = @{ uuid = 'snap-0003'; name = 'weekly.2026-07-12_0300'; create_time = '2026-07-12T03:00:00+00:00'; expiry_time = '2026-08-12T03:00:00+00:00'; snapmirror_label = 'weekly'; size = 262144 }

function JobBody([string]$Uuid, [string]$Href) {
    Json @{ job = @{ uuid = $Uuid; _links = @{ self = @{ href = $Href } } } }
}
function JobState([string]$Uuid, [string]$JState, [hashtable]$JError) {
    $doc = @{ uuid = $Uuid; state = $JState; message = $JState }
    if ($JError) { $doc.error = $JError }
    Json $doc
}
function ErrorBody([string]$Code, [string]$Message) {
    Json @{ error = @{ code = $Code; message = $Message } }
}

$Cred = [pscredential]::new($User, (ConvertTo-SecureString $PlainPass -AsPlainText -Force))

try {
    # ---- session hygiene ---------------------------------------------------
    $Session = New-OntapSession -BaseUri "$BaseUri/" -Credential $Cred
    Check ($Session.BaseUri -ceq $BaseUri) 'session trims the trailing slash from BaseUri'
    Check ($Session.AuthHeader -ceq $ExpectedAuth) 'session builds the documented basic auth header'
    Check ($Session.PSObject.Properties.Name -notcontains 'Password') 'session exposes no Password property'
    Check ((Json $Session) -notmatch [regex]::Escape($PlainPass)) 'serialized session never contains the plaintext password'

    # ---- paginated inventory ----------------------------------------------
    Reset-Mock @{
        "GET $SnapBase" = @(
            @{ Status = 200; Body = (SnapshotPage @($Snap5, $Snap6) $Next1) },
            @{ Status = 200; Body = (SnapshotPage @($Snap1, $Snap2) $Next2) },
            @{ Status = 200; Body = (SnapshotPage @($Snap4, $Snap3) $null) }
        )
    }
    $Inv = Get-OntapSnapshotInventory -Session $Session -VolumeUuid $VolUuid `
        -Fields $FieldList -MaxRecords 2 -OrderBy 'create_time asc'
    $Records = @($Inv.Records)
    Check ($Inv.Pages -eq 3) "inventory walks 3 pages (got $($Inv.Pages))"
    Check ($Records.Count -eq 6) "inventory collects 6 records (got $($Records.Count))"
    Check ($Inv.NumRecords -eq 6) 'NumRecords totals the collected records'
    Check ($Records[0].name -ceq 'manual.2026-07-01_1200') 'server page order preserved (first record)'
    Check ($Records[5].name -ceq 'weekly.2026-07-12_0300') 'server page order preserved (last record)'
    $Log = @($State.Log)
    Check ($Log.Count -eq 3) "exactly 3 page requests (got $($Log.Count))"
    Check (@($Log | Where-Object { $_.Auth -cne $ExpectedAuth }).Count -eq 0) 'every page request carries basic auth'
    Check ($Log[0].Path -ceq $SnapBase) 'first page hits the documented snapshots path'
    $Q = Parse-QueryString $Log[0].RawUrl
    Check ($Q['fields'] -ceq $FieldsJoined) "first page requests fields=$FieldsJoined"
    Check ($Q['max_records'] -ceq '2') 'first page requests max_records=2'
    Check ($Q['order_by'] -ceq 'create_time asc') 'first page requests order_by=create_time asc'
    Check ($Log[1].RawUrl -ceq $Next1) 'second request follows _links.next.href byte-for-byte'
    Check ($Log[2].RawUrl -ceq $Next2) 'third request follows the second next href byte-for-byte'

    # ---- retention selection ----------------------------------------------
    $AsOf = [datetime]::new(2026, 7, 15, 0, 0, 0, [System.DateTimeKind]::Utc)
    $All = @(Select-OntapSnapshotsForDeletion -Snapshots $Records -AsOf $AsOf)
    Check ($All.Count -eq 3) "unlabelled selection keeps 3 snapshots (got $($All.Count))"
    Check ($All[0].uuid -ceq 'snap-0006') 'selection ordered by create_time (first)'
    Check ($All[1].uuid -ceq 'snap-0001') 'selection ordered by create_time (second)'
    Check ($All[2].uuid -ceq 'snap-0002') 'selection ordered by create_time (third)'
    Check (@($All | Where-Object { $_.uuid -ceq 'snap-0003' }).Count -eq 0) 'unexpired snapshot never selected'
    Check (@($All | Where-Object { $_.uuid -ceq 'snap-0004' }).Count -eq 0) 'owned snapshot never selected'
    Check (@($All | Where-Object { $_.uuid -ceq 'snap-0005' }).Count -eq 0) 'snapshot without expiry_time never selected'
    $Daily = @(Select-OntapSnapshotsForDeletion -Snapshots $Records -AsOf $AsOf -SnapmirrorLabel 'daily')
    Check ($Daily.Count -eq 2) "label-filtered selection keeps 2 snapshots (got $($Daily.Count))"
    Check ($Daily[0].uuid -ceq 'snap-0001' -and $Daily[1].uuid -ceq 'snap-0002') 'label filter keeps only matching, ordered'
    $DailyUpper = @(Select-OntapSnapshotsForDeletion -Snapshots $Records -AsOf $AsOf -SnapmirrorLabel 'Daily')
    Check ($DailyUpper.Count -eq 0) 'label match is case-sensitive (ordinal)'

    # ---- deletion with per-snapshot jobs -----------------------------------
    Reset-Mock @{
        "DELETE $SnapBase/snap-0001" = @( @{ Status = 202; Body = (JobBody 'job-del-01' $JobHref1) } )
        "DELETE $SnapBase/snap-0002" = @( @{ Status = 202; Body = (JobBody 'job-del-02' $JobHref2) } )
        'GET /api/cluster/jobs/job-del-01' = @(
            @{ Status = 200; Body = (JobState 'job-del-01' 'running' $null) },
            @{ Status = 200; Body = (JobState 'job-del-01' 'success' $null) }
        )
        'GET /api/cluster/jobs/job-del-02' = @(
            @{ Status = 200; Body = (JobState 'job-del-02' 'failure' @{ code = '1638555'; message = 'The specified snapshot has not expired or is locked' }) }
        )
    }
    $SleepLog = [System.Collections.ArrayList]::new()
    $Results = @(Remove-OntapSnapshots -Session $Session -VolumeUuid $VolUuid -Snapshots $Daily `
        -PollLimit 5 -Sleep { param($Seconds) $null = $SleepLog.Add($Seconds) })
    Check ($Results.Count -eq 2) "one result per selected snapshot (got $($Results.Count))"
    Check ($Results[0].Uuid -ceq 'snap-0001' -and $Results[0].Deleted) 'first snapshot deleted after its job succeeds'
    Check ($null -eq $Results[0].ErrorCode) 'successful delete carries no error code'
    Check (-not $Results[1].Deleted) 'locked snapshot reported as not deleted'
    Check ($Results[1].ErrorCode -ceq '1638555') 'failed job error code surfaced'
    Check ($Results[1].ErrorMessage -match 'not expired or is locked') 'failed job error message surfaced'
    $Log = @($State.Log)
    $Deletes = @($Log | Where-Object { $_.Method -ceq 'DELETE' })
    Check ($Deletes.Count -eq 2) "exactly the selected snapshots are DELETEd (got $($Deletes.Count))"
    Check ($Deletes[0].Path -ceq "$SnapBase/snap-0001") 'DELETE path addresses snapshot by uuid'
    Check (((Parse-QueryString $Deletes[0].RawUrl)['return_timeout']) -ceq '0') 'DELETE uses return_timeout=0'
    Check (((Parse-QueryString $Deletes[1].RawUrl)['return_timeout']) -ceq '0') 'second DELETE uses return_timeout=0'
    $Polls = @($Log | Where-Object { $_.Path -like '/api/cluster/jobs/*' })
    Check ($Polls.Count -eq 3) "job polls: two for the slow job, one for the failed job (got $($Polls.Count))"
    Check ($Polls[0].RawUrl -ceq $JobHref1 -and $Polls[1].RawUrl -ceq $JobHref1) 'job polled on the exact returned href'
    Check ($Polls[2].RawUrl -ceq $JobHref2) 'second job polled on its exact href'
    Check (@($SleepLog).Count -eq 1 -and $SleepLog[0] -eq 1) 'injected sleep called once, with 1 second'
    Check ($Log.Count -eq 5) "no extra requests during deletion (got $($Log.Count))"

    # ---- HTTP-level HAL error aborts the run -------------------------------
    Reset-Mock @{
        "DELETE $SnapBase/snap-0001" = @( @{ Status = 403; Body = (ErrorBody '6' 'not authorized for that command') } )
    }
    $Thrown = $null
    try {
        $null = Remove-OntapSnapshots -Session $Session -VolumeUuid $VolUuid -Snapshots $Daily `
            -PollLimit 5 -Sleep { param($Seconds) }
    } catch { $Thrown = $_ }
    Check ($null -ne $Thrown) '403 on DELETE throws'
    Check ($Thrown.Exception.Message -match '\b6\b' -and $Thrown.Exception.Message -match 'not authorized') 'thrown message carries the HAL error code and message'
    Check ($Thrown.Exception.Message -notmatch [regex]::Escape($PlainPass)) 'thrown message never contains the password'
    Check (@($State.Log).Count -eq 1) 'run aborts before any further snapshot is touched'

    # ---- poll limit --------------------------------------------------------
    Reset-Mock @{
        "DELETE $SnapBase/snap-0001" = @( @{ Status = 202; Body = (JobBody 'job-del-03' $JobHref3) } )
        'GET /api/cluster/jobs/job-del-03' = @(
            @{ Status = 200; Body = (JobState 'job-del-03' 'running' $null) },
            @{ Status = 200; Body = (JobState 'job-del-03' 'running' $null) },
            @{ Status = 200; Body = (JobState 'job-del-03' 'running' $null) }
        )
    }
    $SleepLog = [System.Collections.ArrayList]::new()
    $Results = @(Remove-OntapSnapshots -Session $Session -VolumeUuid $VolUuid -Snapshots @($Daily[0]) `
        -PollLimit 3 -Sleep { param($Seconds) $null = $SleepLog.Add($Seconds) })
    Check ($Results.Count -eq 1 -and -not $Results[0].Deleted) 'exhausted poll limit reports not deleted'
    Check ($Results[0].ErrorMessage -match 'poll limit') 'poll-limit exhaustion names the poll limit'
    Check (@($State.Log | Where-Object { $_.Path -like '/api/cluster/jobs/*' }).Count -eq 3) 'exactly PollLimit polls issued'
    Check (@($SleepLog).Count -eq 2) 'sleeps only between polls under the limit'

    # ---- empty inventory ----------------------------------------------------
    Reset-Mock @{
        "GET $SnapBase" = @( @{ Status = 200; Body = (SnapshotPage @() $null) } )
    }
    $Empty = Get-OntapSnapshotInventory -Session $Session -VolumeUuid $VolUuid `
        -Fields $FieldList -MaxRecords 2 -OrderBy 'create_time asc'
    Check (@($Empty.Records).Count -eq 0) 'empty volume yields zero records'
    Check ($Empty.Pages -eq 1) 'empty volume still counts its single page'
    $NoneSelected = @(Select-OntapSnapshotsForDeletion -Snapshots @($Empty.Records) -AsOf $AsOf)
    Check ($NoneSelected.Count -eq 0) 'selection over nothing selects nothing'
    $State.Log.Clear()
    $NoResults = @(Remove-OntapSnapshots -Session $Session -VolumeUuid $VolUuid -Snapshots @() `
        -PollLimit 3 -Sleep { param($Seconds) })
    Check ($NoResults.Count -eq 0) 'removing an empty selection returns no results'
    Check (@($State.Log).Count -eq 0) 'removing an empty selection issues no requests'
}
finally {
    if ($null -ne $State.Listener) { $State.Listener.Stop() }
    try { $null = $Server.EndInvoke($ServerHandle) } catch { Write-Host "server thread: $_" }
    $Server.Dispose()
    $Runspace.Dispose()
}

Write-Host "passed=$($script:Passed) failed=$($script:Failed)"
if ($script:Failed -gt 0) { exit 1 }
exit 0
