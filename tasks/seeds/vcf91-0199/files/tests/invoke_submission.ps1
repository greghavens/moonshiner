[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $ManifestPath,

    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

Import-Module $ManifestPath -Force -ErrorAction Stop

$connection = $null
try {
    $connection = Connect-VcfInstallerServer `
        -Server '127.0.0.1' `
        -Port $Port `
        -User 'seed-user' `
        -Password 'seed-password' `
        -NotDefault `
        -ErrorAction Stop

    $tasks = @(
        Get-VcfInstallerTaskInventory `
            -Server $connection `
            -PageSize 3 `
            -TaskStatus 'FAILED' `
            -ErrorAction Stop
    )

    $fullyBoundTasks = @(
        Get-VcfInstallerTaskInventory `
            -Server $connection `
            -PageSize 100 `
            -TaskStatus 'FAILED' `
            -TaskType 'HOST_COMMISSION' `
            -ResourceId 'resource-42' `
            -ResourceType 'HOST' `
            -CompletedAfter 0 `
            -TaskName 'Contract' `
            -DoLiveRefresh:$false `
            -ErrorAction Stop
    )
    if ($fullyBoundTasks.Count -ne $tasks.Count) {
        throw 'The fully bound filter call did not return the complete collection.'
    }

    $defaultPageTasks = @(
        Get-VcfInstallerTaskInventory `
            -Server $connection `
            -TaskStatus 'FAILED' `
            -ErrorAction Stop
    )
    if ($defaultPageTasks.Count -ne $tasks.Count) {
        throw 'The default page-size call did not return the complete collection.'
    }

    foreach ($invalidPageSize in @(0, 101)) {
        $rejected = $false
        try {
            Get-VcfInstallerTaskInventory `
                -Server $connection `
                -PageSize $invalidPageSize `
                -ErrorAction Stop | Out-Null
        }
        catch {
            $rejected = $true
        }
        if (-not $rejected) {
            throw "The inventory function accepted page size $invalidPageSize."
        }
    }

    foreach ($malformedPage in @(
        @{ TaskName = 'MOONSHINER_MISSING_METADATA'; Description = 'missing metadata' },
        @{ TaskName = 'MOONSHINER_WRONG_PAGE'; Description = 'an inconsistent page number' }
    )) {
        $rejected = $false
        try {
            Get-VcfInstallerTaskInventory `
                -Server $connection `
                -PageSize 3 `
                -TaskName $malformedPage.TaskName `
                -ErrorAction Stop | Out-Null
        }
        catch {
            $rejected = $true
        }
        if (-not $rejected) {
            throw "The inventory function accepted $($malformedPage.Description)."
        }
    }

    $projection = @(
        $tasks | ForEach-Object {
            [ordered]@{
                id                = [string] $_.Id
                creationTimestamp = [string] $_.CreationTimestamp
                status            = [string] $_.Status
            }
        }
    )
    [System.IO.File]::WriteAllText(
        $OutputPath,
        ($projection | ConvertTo-Json -Depth 5 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
}
finally {
    if ($null -ne $connection) {
        Disconnect-VcfInstallerServer -Server $connection -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
    }
}
