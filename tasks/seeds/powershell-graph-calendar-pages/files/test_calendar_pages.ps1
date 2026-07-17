# Acceptance harness for GraphCalendarExport.psm1.
# Run from the workspace root:  pwsh -NoProfile -File test_calendar_pages.ps1
# Drives the module against a loopback fake Graph calendarView endpoint
# (mock_graph.py). Protected — do not modify this file, mock_graph.py, or
# anything under docs/.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$moduleFile = Join-Path $PSScriptRoot 'GraphCalendarExport.psm1'
if (-not (Test-Path -LiteralPath $moduleFile -PathType Leaf)) {
    Write-Output 'FAIL GraphCalendarExport.psm1 not found in the workspace root'
    exit 1
}

$TOKEN = 'dummy-token-c91f44'   # dummy; never a real credential
$USER = 'u-ravi'
$START = '2026-06-01T00:00:00Z'
$END = '2026-06-08T00:00:00Z'
$SKIP2 = '$skiptoken=cal-pg2-b2Zmc2V0%3D'
$SKIP3 = '$skiptoken=cal-pg3-b2Zmc2V0%3D'
$ORDERED_IDS = 'e-kickoff,e-standup-0602,e-standup-0604,e-allhands,e-standup-0606,e-inventory'

$T = Join-Path $PSScriptRoot '_t'
$script:checks = 0
$script:fails = 0
$script:delays = [System.Collections.Generic.List[int]]::new()
$delayCommand = { $null = $script:delays.Add([int]$args[0]) }

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
        -ArgumentList @((Join-Path $PSScriptRoot 'mock_graph.py'), $portFile) `
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
    $graphBase = "$baseUrl/v1.0"

    function Get-MockLog {
        (Invoke-RestMethod -Uri "$baseUrl/__log__" -Method Get) | ForEach-Object { $_ }
    }
    function Reset-Mock {
        Invoke-RestMethod -Uri "$baseUrl/__reset__" -Method Post -Body '{}' -ContentType 'application/json' > $null
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

    # ---- UTC fetch: range params, paging, normalization ---------------------
    $session = Connect-GraphCalendar -BaseUrl $graphBase -AccessToken $TOKEN `
        -DelayCommand $delayCommand -MaxRetries 3
    $events = @(Get-GraphCalendarView -Session $session -UserId $USER -StartUtc $START -EndUtc $END)

    Assert-Eq 'six unique events after dedup across pages' 6 $events.Count
    Assert-Eq 'events sorted by start then id' $ORDERED_IDS (($events | ForEach-Object { $_.id }) -join ',')
    Assert-Eq 'normalized key order' 'id,iCalUId,subject,type,seriesMasterId,start,end,timeZone,isAllDay,organizer' `
        (($events[0].PSObject.Properties.Name) -join ',')

    $kickoff = $events[0]
    Assert-Eq 'kickoff subject' 'Fitout kickoff' $kickoff.subject
    Assert-Eq 'kickoff type' 'singleInstance' $kickoff.type
    Assert-True 'kickoff has no seriesMasterId' ($null -eq $kickoff.seriesMasterId)
    Assert-Eq 'kickoff start trims the 7-digit fraction' '2026-06-01T13:00:00' $kickoff.start
    Assert-Eq 'kickoff end' '2026-06-01T14:00:00' $kickoff.end
    Assert-Eq 'kickoff timeZone' 'UTC' $kickoff.timeZone
    Assert-True 'kickoff is not all-day' ($kickoff.isAllDay -eq $false)
    Assert-Eq 'kickoff organizer flattens to the address' 'mia.tran@northline.example' $kickoff.organizer
    Assert-Eq 'kickoff iCalUId preserved' '040000008200E00074C5B7101A82E008KICKOFF01' $kickoff.iCalUId

    $standup = $events[1]
    Assert-Eq 'occurrence type survives normalization' 'occurrence' $standup.type
    Assert-Eq 'occurrence keeps seriesMasterId' 'sm-standup' $standup.seriesMasterId
    Assert-Eq 'occurrence start' '2026-06-02T14:30:00' $standup.start
    Assert-Eq 'occurrences of one series share iCalUId' '040000008200E00074C5B7101A82E008STANDUP01' $standup.iCalUId

    $inventory = $events[5]
    Assert-True 'all-day flag preserved' ($inventory.isAllDay -eq $true)
    Assert-Eq 'all-day start' '2026-06-07T00:00:00' $inventory.start

    $log = @(Get-MockLog)
    Assert-Eq 'three requests for three pages' 3 $log.Count
    foreach ($entry in $log) {
        Assert-Eq "bearer auth on $($entry.query)" "Bearer $TOKEN" $entry.auth
        Assert-Eq "Prefer header on $($entry.query)" 'outlook.timezone="UTC"' $entry.prefer
    }
    $decoded = [System.Uri]::UnescapeDataString($log[0].query)
    Assert-True 'initial request carries the UTC start' ($decoded.Contains("startDateTime=$START"))
    Assert-True 'initial request carries the UTC end' ($decoded.Contains("endDateTime=$END"))
    Assert-Eq 'page 2 nextLink reused verbatim' $SKIP2 $log[1].query
    Assert-Eq 'page 3 nextLink reused verbatim' $SKIP3 $log[2].query

    # ---- requested timezone flows through the Prefer header ------------------
    Reset-Mock
    $pstEvents = @(Get-GraphCalendarView -Session $session -UserId $USER `
        -StartUtc $START -EndUtc $END -TimeZone 'Pacific Standard Time')
    Assert-Eq 'PST run keeps the same event order' $ORDERED_IDS (($pstEvents | ForEach-Object { $_.id }) -join ',')
    Assert-Eq 'PST kickoff start converted' '2026-06-01T06:00:00' $pstEvents[0].start
    Assert-Eq 'PST kickoff timeZone label' 'Pacific Standard Time' $pstEvents[0].timeZone
    Assert-Eq 'PST occurrence start converted' '2026-06-02T07:30:00' $pstEvents[1].start
    Assert-Eq 'PST all-day start shifts with the zone' '2026-06-06T17:00:00' $pstEvents[5].start
    $pstLog = @(Get-MockLog)
    foreach ($entry in $pstLog) {
        Assert-Eq "PST Prefer header on $($entry.query)" 'outlook.timezone="Pacific Standard Time"' $entry.prefer
    }

    # ---- export: byte-stable JSON regardless of API ordering -----------------
    Reset-Mock
    $path1 = Join-Path $T 'export1.json'
    $path2 = Join-Path $T 'export2.json'
    $result1 = Export-GraphCalendarReport -Session $session -UserId $USER `
        -StartUtc $START -EndUtc $END -Path $path1
    $result2 = Export-GraphCalendarReport -Session $session -UserId $USER `
        -StartUtc $START -EndUtc $END -Path $path2

    Assert-Eq 'export status complete' 'complete' $result1.Status
    Assert-Eq 'export pages fetched' 3 $result1.PagesFetched
    Assert-Eq 'export events exported' 6 $result1.EventsExported
    Assert-True 'complete export has no resume link' ($null -eq $result1.ResumeLink)
    Assert-True 'complete export has no retry-after' ($null -eq $result1.RetryAfterSeconds)
    $hash1 = (Get-FileHash -LiteralPath $path1 -Algorithm SHA256).Hash
    $hash2 = (Get-FileHash -LiteralPath $path2 -Algorithm SHA256).Hash
    Assert-Eq 'exports are byte-identical across API orderings' $hash1 $hash2

    # -DateKind String: assert on the exact serialized strings, not DateTimes.
    $report = Get-Content -LiteralPath $path1 -Raw | ConvertFrom-Json -DateKind String
    Assert-Eq 'report key order' 'user,range,timeZone,eventCount,events' `
        (($report.PSObject.Properties.Name) -join ',')
    Assert-Eq 'report user' $USER $report.user
    Assert-Eq 'report range start' $START $report.range.start
    Assert-Eq 'report range end' $END $report.range.end
    Assert-Eq 'report timeZone' 'UTC' $report.timeZone
    Assert-Eq 'report event count' 6 $report.eventCount
    Assert-Eq 'report events sorted' $ORDERED_IDS ((@($report.events) | ForEach-Object { $_.id }) -join ',')

    # ---- one throttled page recovers after Retry-After ------------------------
    Reset-Mock
    Invoke-Control '/__throttle__' '{"token":"cal-pg2","times":1,"retry_after":4}'
    $script:delays.Clear()
    $throttledEvents = @(Get-GraphCalendarView -Session $session -UserId $USER -StartUtc $START -EndUtc $END)
    Assert-Eq 'throttled run still yields all events' 6 $throttledEvents.Count
    Assert-Eq 'waited exactly the Retry-After' '4' ($script:delays -join ',')
    $throttleLog = @(Get-MockLog | Where-Object { $_.query -ceq $SKIP2 })
    Assert-Eq 'throttled URL repeated verbatim' 2 $throttleLog.Count

    # ---- throttling exhaustion: partial progress, not silence ----------------
    Reset-Mock
    Invoke-Control '/__throttle__' '{"token":"cal-pg2","times":99,"retry_after":3}'
    $script:delays.Clear()
    $limited = Connect-GraphCalendar -BaseUrl $graphBase -AccessToken $TOKEN `
        -DelayCommand $delayCommand -MaxRetries 2
    $partialPath = Join-Path $T 'partial.json'
    $partial = Export-GraphCalendarReport -Session $limited -UserId $USER `
        -StartUtc $START -EndUtc $END -Path $partialPath

    Assert-Eq 'partial status' 'partial' $partial.Status
    Assert-Eq 'partial pages fetched' 1 $partial.PagesFetched
    Assert-Eq 'partial events exported' 2 $partial.EventsExported
    Assert-True 'partial resume link points at the stuck page' ("$($partial.ResumeLink)".Contains('cal-pg2'))
    Assert-Eq 'partial retry-after diagnostic' 3 $partial.RetryAfterSeconds
    Assert-Eq 'exhaustion waited MaxRetries times' '3,3' ($script:delays -join ',')
    $stuckHits = @(Get-MockLog | Where-Object { $_.query -ceq $SKIP2 })
    Assert-Eq 'initial attempt plus MaxRetries retries' 3 $stuckHits.Count

    $partialReport = Get-Content -LiteralPath $partialPath -Raw | ConvertFrom-Json -DateKind String
    Assert-Eq 'partial file key order' 'partial,user,range,timeZone,pagesFetched,eventCount,resumeLink,retryAfterSeconds,events' `
        (($partialReport.PSObject.Properties.Name) -join ',')
    Assert-True 'partial file flags itself' ($partialReport.partial -eq $true)
    Assert-Eq 'partial file event count' 2 $partialReport.eventCount
    Assert-Eq 'partial file keeps the collected events' 'e-kickoff,e-standup-0602' `
        ((@($partialReport.events) | ForEach-Object { $_.id }) -join ',')
    Assert-True 'partial file resume link recorded' ("$($partialReport.resumeLink)".Contains('cal-pg2'))
    Reset-Mock

    # ---- 401 is a distinct, unretried failure --------------------------------
    $badSession = Connect-GraphCalendar -BaseUrl $graphBase -AccessToken 'dummy-wrong-1a2b3c' `
        -DelayCommand $delayCommand -MaxRetries 3
    $caught = $null
    try {
        Get-GraphCalendarView -Session $badSession -UserId $USER -StartUtc $START -EndUtc $END > $null
    } catch {
        $caught = $_
    }
    Assert-True '401 throws' ($null -ne $caught)
    Assert-Eq '401 throws GraphAuthError' 'GraphAuthError' $caught.Exception.GetType().Name
    Assert-Eq '401 carries the status code' 401 $caught.Exception.StatusCode
    Assert-True '401 message must not contain any token' `
        (-not ("$($caught.Exception.Message)".Contains($TOKEN)) -and
         -not ("$($caught.Exception.Message)".Contains('dummy-wrong-1a2b3c')))
    Assert-Eq '401 is not retried' 1 @(Get-MockLog).Count

    # ---- 5xx is a distinct, unretried failure --------------------------------
    Reset-Mock
    Invoke-Control '/__mode__' '{"fail":true}'
    $caught = $null
    try {
        Get-GraphCalendarView -Session $session -UserId $USER -StartUtc $START -EndUtc $END > $null
    } catch {
        $caught = $_
    }
    Assert-True '503 throws' ($null -ne $caught)
    Assert-Eq '503 throws GraphServerError' 'GraphServerError' $caught.Exception.GetType().Name
    Assert-Eq '503 carries the status code' 503 $caught.Exception.StatusCode
    Assert-Eq '503 is not retried' 1 @(Get-MockLog).Count
    Invoke-Control '/__mode__' '{"fail":false}'
} finally {
    if ($null -ne $srv -and -not $srv.HasExited) {
        Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "checks=$($script:checks) fails=$($script:fails)"
if ($script:fails -gt 0) { exit 1 }
Write-Output 'ALL TESTS PASSED'
exit 0
