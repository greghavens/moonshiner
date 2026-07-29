# Stable SDDC Manager domain inventory using the VMware.Sdk.Vcf PowerCLI binding.
Set-StrictMode -Version Latest

function Get-VcfDomainInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Server,
        [Parameter(Mandatory)][PSCredential]$Credential,
        [ValidateRange(1, 65535)][int]$Port = 443,
        [ValidateSet('http', 'https')][string]$Protocol = 'https',
        [int]$PageSize = 100,
        [string]$Type
    )

    if ($PageSize -le 0) {
        throw [System.ArgumentOutOfRangeException]::new(
            'PageSize', $PageSize, 'PageSize must be greater than zero.')
    }

    $connection = $null
    try {
        $connection = Connect-VcfSddcManagerServer -Server $Server -Port $Port `
            -Protocol $Protocol -Credential $Credential -NotDefault -ErrorAction Stop

        $all = [System.Collections.Generic.List[object]]::new()
        $nextPageNumber = $null
        $expectedTotal = $null

        while ($true) {
            $request = @{
                Server      = $connection
                PageSize    = $PageSize
                ErrorAction = 'Stop'
            }
            if ($PSBoundParameters.ContainsKey('Type')) {
                $request.Type = $Type
            }
            if ($null -ne $nextPageNumber) {
                $request.PageNumber = $nextPageNumber
            }

            $page = Invoke-VcfGetDomains @request
            if ($null -eq $page -or $null -eq $page.PageMetadata) {
                throw 'getDomains returned no pageMetadata.'
            }

            $currentPage = [int]$page.PageMetadata.PageNumber
            $total = [int]$page.PageMetadata.TotalElements
            if ($total -lt 0) {
                throw "getDomains returned a negative totalElements value: $total."
            }
            if ($null -eq $expectedTotal) {
                $expectedTotal = $total
            }
            elseif ($expectedTotal -ne $total) {
                throw "getDomains changed totalElements from $expectedTotal to $total during pagination."
            }

            $before = $all.Count
            foreach ($domain in @($page.Elements)) {
                $all.Add($domain)
            }

            if ($all.Count -ge $expectedTotal) {
                break
            }
            if ($all.Count -eq $before) {
                throw "getDomains page $currentPage made no progress before totalElements was reached."
            }
            if ($currentPage -eq [int]::MaxValue) {
                throw 'getDomains pageNumber cannot be advanced safely.'
            }
            $nextPageNumber = $currentPage + 1
        }

        if ($all.Count -ne $expectedTotal) {
            throw "getDomains returned $($all.Count) elements but declared totalElements $expectedTotal."
        }

        # BUG: the API is allowed to vary element order. Returning page order
        # makes both this function and the JSON export nondeterministic.
        foreach ($domain in $all) {
            [pscustomobject][ordered]@{
                id                    = $domain.Id
                name                  = $domain.Name
                type                  = $domain.Type
                status                = $domain.Status
                isManagementSsoDomain = $domain.IsManagementSsoDomain
            }
        }
    }
    finally {
        if ($null -ne $connection) {
            Disconnect-VcfSddcManagerServer -Server $connection -Force `
                -ErrorAction SilentlyContinue
        }
    }
}

function Export-VcfDomainInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Server,
        [Parameter(Mandatory)][PSCredential]$Credential,
        [Parameter(Mandatory)][string]$Path,
        [ValidateRange(1, 65535)][int]$Port = 443,
        [ValidateSet('http', 'https')][string]$Protocol = 'https',
        [int]$PageSize = 100,
        [string]$Type
    )

    $getArguments = @{
        Server     = $Server
        Credential = $Credential
        Port       = $Port
        Protocol   = $Protocol
        PageSize   = $PageSize
    }
    if ($PSBoundParameters.ContainsKey('Type')) {
        $getArguments.Type = $Type
    }

    $domains = @(Get-VcfDomainInventory @getArguments)
    $document = [ordered]@{ domains = $domains }
    $json = ConvertTo-Json -InputObject $document -Depth 6 -Compress
    $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    [System.IO.File]::WriteAllText(
        $resolved,
        $json + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    return $resolved
}

Export-ModuleMember -Function Get-VcfDomainInventory, Export-VcfDomainInventory
