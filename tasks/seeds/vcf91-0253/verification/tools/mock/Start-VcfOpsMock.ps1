<#
.SYNOPSIS
    A loopback mock of the VCF Operations suite-api, pinned to docs/contract.json.

.DESCRIPTION
    Serves exactly the three operations docs/contract.json names and nothing else.
    Anything else is answered 404 and recorded, so the verifier can tell that the
    module stayed inside the contract.

    Every request is appended to -LogPath as one JSON object per line. The log is
    the verifier's only view of the wire; the mock itself asserts nothing beyond
    what it needs to answer, so a failing assertion reads as "the module sent X"
    rather than "the mock refused".

    Dot-source this file and call Start-VcfOpsMock. It binds 127.0.0.1 only.
#>

Set-StrictMode -Version Latest

function Get-VcfOpsMockFreePort {
    <# Ask the OS for an unused loopback port by binding one and letting go. #>
    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $probe.Start()
    try { return $probe.LocalEndpoint.Port } finally { $probe.Stop() }
}

function Start-VcfOpsMock {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $FixturePath,
        [Parameter(Mandatory)] [string] $LogPath,
        # A broken pager can ask for pages forever. The mock refuses to keep
        # answering so the run fails instead of hanging.
        [int] $MaxResourceRequests = 40
    )

    $fixture = Get-Content -LiteralPath $FixturePath -Raw | ConvertFrom-Json
    Set-Content -LiteralPath $LogPath -Value '' -NoNewline

    $listener = [System.Net.HttpListener]::new()
    $port = 0
    foreach ($attempt in 1..25) {
        $candidate = Get-VcfOpsMockFreePort
        $listener.Prefixes.Clear()
        $listener.Prefixes.Add("http://127.0.0.1:$candidate/")
        try { $listener.Start(); $port = $candidate; break } catch { }
    }
    if ($port -eq 0) { throw 'could not bind a loopback port for the mock' }

    $worker = Start-ThreadJob -Name "vcf-ops-mock-$port" -ScriptBlock {
        param($listener, $fixtureJson, $logPath, $maxResourceRequests)

        Set-StrictMode -Version Latest
        $fixture = $fixtureJson | ConvertFrom-Json
        $token = $fixture.token
        $pages = @($fixture.pages)
        $resources = $fixture.resources
        $seq = 0
        $resourceRequests = 0

        # A resource may be listed on more than one page; the fixture stores each
        # one once and the pages reference it by key.
        function Resolve-Page {
            param($Fixture, $Keys)
            $out = @()
            foreach ($key in $Keys) { $out += , $Fixture.resources.$key }
            return $out
        }

        while ($listener.IsListening) {
            try { $context = $listener.GetContext() } catch { break }

            $request = $context.Request
            $body = ''
            if ($request.HasEntityBody) {
                $reader = [System.IO.StreamReader]::new($request.InputStream, $request.ContentEncoding)
                try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
            }

            # PowerCLI joins its service root and the /suite-api base path into a
            # doubled slash. Collapse it before matching so the route table can
            # be written the way the contract writes the paths.
            $path = [regex]::Replace($request.Url.AbsolutePath, '/{2,}', '/')
            $rawUrl = $request.RawUrl
            $rawQuery = ''
            $split = $rawUrl.IndexOf('?')
            if ($split -ge 0) { $rawQuery = $rawUrl.Substring($split + 1) }

            $authorization = $request.Headers['Authorization']
            $status = 200
            $payload = $null
            $served = $null

            $authorized = ($authorization -eq "OpsToken $token")

            switch ($path) {

                '/suite-api/api/auth/token/acquire' {
                    $served = 'acquireToken'
                    $credentials = $null
                    try { $credentials = $body | ConvertFrom-Json } catch { }
                    if (-not $credentials -or $credentials.PSObject.Properties.Name -notcontains 'username') {
                        $status = 400
                        $payload = @{ message = 'username-password body required' }
                    }
                    else {
                        $payload = [ordered]@{
                            token     = $token
                            validity  = 1893456000000
                            expiresAt = '2030-01-01T00:00:00.000'
                            roles     = @('ADMIN')
                        }
                    }
                }

                '/suite-api/api/versions/current' {
                    $served = 'getCurrentVersionOfServer'
                    if (-not $authorized) {
                        $status = 401
                        $payload = @{ message = 'missing or unrecognized token' }
                    }
                    else {
                        $payload = [ordered]@{
                            releaseName                = 'VCF Operations 9.1.0.0'
                            major                      = 9
                            minor                      = 1
                            minorMinor                 = 0
                            patch                      = 0
                            buildNumber                = 24518462
                            releasedDate               = 1772568000000
                            humanlyReadableReleaseDate = 'Tuesday, March 3, 2026 at 12:00:00 PM Pacific Standard Time'
                        }
                    }
                }

                '/suite-api/api/resources' {
                    $served = 'getResources'
                    $resourceRequests++
                    $query = [System.Web.HttpUtility]::ParseQueryString($request.Url.Query)
                    $pageValue = $query['page']
                    $pageSizeValue = $query['pageSize']

                    if (-not $authorized) {
                        $status = 401
                        $payload = @{ message = 'missing or unrecognized token' }
                    }
                    elseif ($resourceRequests -gt $maxResourceRequests) {
                        $status = 429
                        $payload = @{ message = "getResources called $resourceRequests times; this collection is $($pages.Count) pages long" }
                    }
                    elseif ([string]::IsNullOrEmpty($pageSizeValue) -or $pageSizeValue -notmatch '^\d+$') {
                        $status = 400
                        $payload = @{ message = "pageSize must be a non-negative integer; got '$pageSizeValue'" }
                    }
                    elseif ([string]::IsNullOrEmpty($pageValue) -or $pageValue -notmatch '^\d+$') {
                        $status = 400
                        $payload = @{ message = "page must be a non-negative integer; got '$pageValue'" }
                    }
                    else {
                        $pageIndex = [int]$pageValue
                        $rows = @()
                        if ($pageIndex -lt $pages.Count) {
                            $rows = @(Resolve-Page -Fixture $fixture -Keys $pages[$pageIndex])
                        }

                        $links = @(
                            [ordered]@{ rel = 'SELF'; href = "/suite-api/api/resources?page=$pageIndex&pageSize=$pageSizeValue" }
                        )
                        if ($pageIndex -gt 0) {
                            $links += [ordered]@{ rel = 'PREVIOUS'; href = "/suite-api/api/resources?page=$($pageIndex - 1)&pageSize=$pageSizeValue" }
                        }
                        # The one authoritative "there is more" signal.
                        if ($pageIndex -lt ($pages.Count - 1)) {
                            $links += [ordered]@{ rel = 'NEXT'; href = "/suite-api/api/resources?page=$($pageIndex + 1)&pageSize=$pageSizeValue" }
                        }

                        $payload = [ordered]@{
                            links        = $links
                            pageInfo     = [ordered]@{
                                page       = $pageIndex
                                pageSize   = [int]$pageSizeValue
                                totalCount = $fixture.reportedTotalCount
                            }
                            resourceList = $rows
                        }
                    }
                }

                default {
                    $status = 404
                    $payload = @{ message = "operation not in docs/contract.json: $($request.HttpMethod) $path" }
                }
            }

            # A route the contract names, reached with the wrong method, is still
            # outside the contract.
            $expectedMethod = @{
                'acquireToken'              = 'POST'
                'getCurrentVersionOfServer' = 'GET'
                'getResources'              = 'GET'
            }
            if ($served -and $request.HttpMethod -ne $expectedMethod[$served]) {
                $status = 405
                $payload = @{ message = "$served is $($expectedMethod[$served]), not $($request.HttpMethod)" }
                $served = $null
            }

            $seq++
            $entry = [ordered]@{
                seq           = $seq
                served        = $served
                method        = $request.HttpMethod
                path          = $path
                rawUrl        = $rawUrl
                query         = $rawQuery
                body          = $body
                authorization = $authorization
                accept        = $request.Headers['Accept']
                contentType   = $request.ContentType
                status        = $status
            }
            Add-Content -LiteralPath $logPath -Value ($entry | ConvertTo-Json -Depth 4 -Compress)

            $json = if ($null -eq $payload) { '' } else { $payload | ConvertTo-Json -Depth 8 -Compress }
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
            $context.Response.StatusCode = $status
            $context.Response.ContentType = 'application/json'
            $context.Response.ContentLength64 = $bytes.Length
            try {
                $context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
                $context.Response.Close()
            }
            catch { }
        }
    } -ArgumentList $listener, (Get-Content -LiteralPath $FixturePath -Raw), $LogPath, $MaxResourceRequests

    return [pscustomobject]@{
        Port     = $port
        LogPath  = $LogPath
        Fixture  = $fixture
        Listener = $listener
        Worker   = $worker
    }
}

function Stop-VcfOpsMock {
    [CmdletBinding()]
    param([Parameter(Mandatory)] $Mock)

    try { $Mock.Listener.Stop() } catch { }
    try { $Mock.Listener.Close() } catch { }
    try { $Mock.Worker | Stop-Job -ErrorAction SilentlyContinue } catch { }
    try { $Mock.Worker | Remove-Job -Force -ErrorAction SilentlyContinue } catch { }
}

function Get-VcfOpsMockRequests {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $LogPath)

    if (-not (Test-Path -LiteralPath $LogPath)) { return @() }
    return @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { $_.Trim() } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
}
