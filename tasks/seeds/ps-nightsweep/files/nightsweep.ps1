# nightsweep.ps1 — run the nightly sweep plan from the retention planner.
# sweep.json is a list of tasks: archive tasks copy a file into the archive
# tree, purge tasks delete expired staging files. Every task is reported and
# the run exits 65 when any task failed so cron pages.
# Usage: pwsh -NoProfile -File nightsweep.ps1 -Plan <sweep.json> -Root <dir>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Plan,
    [Parameter(Mandatory)][string]$Root
)

Set-StrictMode -Version Latest

if (-not (Test-Path -LiteralPath $Plan -PathType Leaf)) {
    [Console]::Error.WriteLine("nightsweep: plan not found: $Plan")
    exit 66
}

$tasks = @(Get-Content -LiteralPath $Plan -Raw | ConvertFrom-Json)
$logPath = Join-Path $Root 'sweep.log'

$failed = 0
foreach ($task in $tasks) {
    $ok = $true
    switch ($task.action) {
        'archive' {
            try {
                $dest = Join-Path $Root $task.dest
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) > $null
                Copy-Item -Path (Join-Path $Root $task.src) -Destination $dest
            } catch {
                $ok = $false
            }
        }
        'purge' {
            Remove-Item -Path (Join-Path $Root $task.src)
            Add-Content -Path $logPath -Value ('purge {0}' -f $task.name)
            $ok = $?
        }
    }
    if ($ok) {
        Write-Output ('task {0}: ok' -f $task.name)
    } else {
        $failed++
        Write-Output ('task {0}: FAILED' -f $task.name)
    }
}
Write-Output ('swept: {0} tasks, {1} failed' -f $tasks.Count, $failed)
if ($failed -gt 0) { exit 65 }
exit 0
