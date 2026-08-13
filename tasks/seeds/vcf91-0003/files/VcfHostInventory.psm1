# Stable SDDC Manager host inventory using VMware's generated PowerCLI SDK.
Set-StrictMode -Version Latest

function Get-VcfHostInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object]$Server,

        [ValidateRange(1, 2147483647)]
        [int]$PageSize = 100,

        [string]$Status
    )

    if (
        $PSBoundParameters.ContainsKey('Status') -and
        [string]::IsNullOrWhiteSpace($Status)
    ) {
        throw [System.ArgumentException]::new(
            'Status cannot be blank when supplied.',
            'Status'
        )
    }

    $all = [System.Collections.Generic.List[object]]::new()
    $nextPageNumber = $null
    $expectedTotal = $null
    $seenPages = [System.Collections.Generic.HashSet[int]]::new()

    while ($true) {
        $request = @{
            Server      = $Server
            PageSize    = $PageSize
            ErrorAction = 'Stop'
        }
        if ($PSBoundParameters.ContainsKey('Status')) {
            $request.Status = $Status
        }
        if ($null -ne $nextPageNumber) {
            $request.PageNumber = $nextPageNumber
        }

        $page = Invoke-VcfGetHosts @request
        if ($null -eq $page -or $null -eq $page.PageMetadata) {
            throw 'getHosts returned no pageMetadata.'
        }

        $currentPage = [int]$page.PageMetadata.PageNumber
        $total = [int]$page.PageMetadata.TotalElements
        if ($total -lt 0) {
            throw "getHosts returned a negative totalElements value: $total."
        }
        if ($null -eq $expectedTotal) {
            $expectedTotal = $total
        }
        elseif ($expectedTotal -ne $total) {
            throw "getHosts changed totalElements from $expectedTotal to $total during pagination."
        }
        if (-not $seenPages.Add($currentPage)) {
            throw "getHosts repeated pageNumber $currentPage."
        }

        $before = $all.Count
        foreach ($hostRecord in @($page.Elements)) {
            $all.Add($hostRecord)
        }

        if ($all.Count -gt $expectedTotal) {
            throw "getHosts returned more elements than totalElements $expectedTotal."
        }
        if ($all.Count -eq $expectedTotal) {
            break
        }
        if ($all.Count -eq $before) {
            throw "getHosts page $currentPage made no progress before totalElements was reached."
        }
        if ($currentPage -eq [int]::MaxValue) {
            throw 'getHosts pageNumber cannot be advanced safely.'
        }
        $nextPageNumber = $currentPage + 1
    }

    # BUG: SDDC Manager does not promise collection order. Projecting the
    # accumulated page order makes both this result and the JSON export drift.
    foreach ($hostRecord in $all) {
        [pscustomobject][ordered]@{
            id                  = $hostRecord.Id
            fqdn                = $hostRecord.Fqdn
            status              = $hostRecord.Status
            isStandalone        = $hostRecord.IsStandalone
            isLifecycleManaged  = $hostRecord.IsLifecycleManaged
            isVsanWitnessHost   = $hostRecord.IsVsanWitnessHost
        }
    }
}

function Export-VcfHostInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object]$Server,

        [Parameter(Mandatory)]
        [string]$Path,

        [ValidateRange(1, 2147483647)]
        [int]$PageSize = 100,

        [string]$Status
    )

    $getArguments = @{
        Server   = $Server
        PageSize = $PageSize
    }
    if ($PSBoundParameters.ContainsKey('Status')) {
        $getArguments.Status = $Status
    }

    $hosts = @(Get-VcfHostInventory @getArguments)
    $document = [ordered]@{ hosts = $hosts }
    $json = ConvertTo-Json -InputObject $document -Depth 5 -Compress
    $resolved =
        $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
            $Path
        )
    [System.IO.File]::WriteAllText(
        $resolved,
        $json + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    return $resolved
}

Export-ModuleMember -Function Get-VcfHostInventory, Export-VcfHostInventory
