#Requires -Version 7
# Acceptance harness for the MerakiPages module: loopback fakes of the Cisco
# Meraki Dashboard API v1 pinned by docs/contract.json (provenance in
# docs/official_sources.json). Hermetic: no real dashboard, no real API key,
# no real sleeping. Protected file -- do not modify.
# Run: pwsh -NoProfile -File test_meraki_pages.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Checks = 0
function Assert-True {
    param(
        [Parameter(Mandatory)][bool]$Condition,
        [Parameter(Mandatory)][string]$Message
    )
    $script:Checks++
    if (-not $Condition) {
        throw ("check {0} failed: {1}" -f $script:Checks, $Message)
    }
}

$ApiKey = 'abcd1234abcd1234abcd1234abcd1234abcd1234-fake'

function Get-FreePort {
    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $probe.Start()
    $port = ([System.Net.IPEndPoint]$probe.LocalEndpoint).Port
    $probe.Stop()
    return $port
}

function Start-FakeServer {
    $port = Get-FreePort
    $state = [hashtable]::Synchronized(@{
        Requests = [System.Collections.ArrayList]::Synchronized([System.Collections.ArrayList]::new())
        Routes   = [hashtable]::Synchronized(@{})
    })
    $listener = [System.Net.HttpListener]::new()
    $listener.Prefixes.Add("http://127.0.0.1:$port/")
    $listener.Start()
    $job = Start-ThreadJob -ScriptBlock {
        param($listener, $state)
        while ($listener.IsListening) {
            try { $ctx = $listener.GetContext() } catch { break }
            $req = $ctx.Request
            $null = $state.Requests.Add(@{
                Method        = $req.HttpMethod
                Path          = $req.Url.AbsolutePath
                Query         = $req.Url.Query
                Authorization = $req.Headers['Authorization']
            })
            $key = '{0} {1}' -f $req.HttpMethod, $req.Url.AbsolutePath
            $queue = $state.Routes[$key]
            if ($null -eq $queue -or $queue.Count -eq 0) {
                $resp = @{ Status = 500; Body = ('{"errors":["unexpected request ' + $key + '"]}'); Headers = @{} }
            } else {
                $resp = $queue[0]
                $queue.RemoveAt(0)
            }
            $out = $ctx.Response
            $out.StatusCode = $resp.Status
            foreach ($name in $resp.Headers.Keys) {
                if ($name -eq 'Location') { $out.RedirectLocation = [string]$resp.Headers[$name] }
                else { $out.Headers.Add($name, [string]$resp.Headers[$name]) }
            }
            $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$resp.Body)
            $out.ContentType = 'application/json'
            $out.ContentLength64 = $bytes.Length
            $out.OutputStream.Write($bytes, 0, $bytes.Length)
            $out.Close()
        }
    } -ArgumentList $listener, $state
    [pscustomobject]@{
        Listener = $listener
        Job      = $job
        State    = $state
        Port     = $port
        BaseUri  = "http://127.0.0.1:$port/api/v1"
    }
}

function Add-Route {
    param($Server, [string]$Key, [int]$Status, [string]$Body, [hashtable]$Headers = @{})
    if (-not $Server.State.Routes.ContainsKey($Key)) {
        $Server.State.Routes[$Key] = [System.Collections.ArrayList]::Synchronized([System.Collections.ArrayList]::new())
    }
    $null = $Server.State.Routes[$Key].Add(@{ Status = $Status; Body = $Body; Headers = $Headers })
}

function Get-Recorded {
    param($Server, [string]$Path)
    return ,@($Server.State.Requests | Where-Object { $_.Path -eq $Path })
}

function Stop-FakeServer {
    param($Server)
    try { $Server.Listener.Stop(); $Server.Listener.Close() } catch { }
    try { $null = Wait-Job -Job $Server.Job -Timeout 5; Remove-Job -Job $Server.Job -Force } catch { }
}

Import-Module (Join-Path $PSScriptRoot 'MerakiPages.psm1') -Force

$serverA = $null
$serverB = $null
$serverC = $null
try {
    $serverA = Start-FakeServer
    $serverB = Start-FakeServer
    $serverC = Start-FakeServer

    $script:Sleeps = [System.Collections.ArrayList]::new()
    $sleepAction = { param($seconds) $null = $script:Sleeps.Add($seconds) }

    # --- context defaults ------------------------------------------------
    $ctxDefault = New-MerakiContext -BaseUri 'https://api.meraki.com/api/v1/' -ApiKey $ApiKey
    Assert-True ($ctxDefault.BaseUri -ceq 'https://api.meraki.com/api/v1') 'BaseUri must be stored without a trailing slash'
    Assert-True ($ctxDefault.RetryLimit -eq 3) 'default RetryLimit must be 3'
    foreach ($suffix in @('.meraki.com', '.meraki.ca', '.meraki.cn', '.meraki.in', '.gov-meraki.com')) {
        Assert-True (@($ctxDefault.TrustedHosts) -contains $suffix) ("default TrustedHosts must include {0}" -f $suffix)
    }

    # --- Link header parsing ---------------------------------------------
    $links = ConvertFrom-LinkHeader -Value '<https://api.meraki.com/api/v1/organizations?perPage=5>; rel=first, <https://api.meraki.com/api/v1/organizations?perPage=5&startingAfter=8100>; rel=next'
    Assert-True ($links.Count -eq 2) 'unquoted rel form must parse into two relations'
    Assert-True ($links['first'] -ceq 'https://api.meraki.com/api/v1/organizations?perPage=5') 'first relation URL must be exact'
    Assert-True ($links['next'] -ceq 'https://api.meraki.com/api/v1/organizations?perPage=5&startingAfter=8100') 'next relation URL must be exact'
    $quoted = ConvertFrom-LinkHeader -Value '<http://x.example/a?s=1>; rel="prev" , <http://x.example/a?s=2>; rel="last"'
    Assert-True ($quoted.Count -eq 2) 'quoted rel form must parse'
    Assert-True ($quoted['prev'] -ceq 'http://x.example/a?s=1') 'quoted prev URL must be exact'
    Assert-True ($quoted['last'] -ceq 'http://x.example/a?s=2') 'quoted last URL must be exact'
    Assert-True ((ConvertFrom-LinkHeader -Value '').Count -eq 0) 'empty Link header parses to no relations'

    # --- bearer auth + JSON decoding -------------------------------------
    $ctxA = New-MerakiContext -BaseUri $serverA.BaseUri -ApiKey $ApiKey -SleepAction $sleepAction
    Add-Route $serverA 'GET /api/v1/organizations' 200 '[{"id":"810001","name":"Aster Labs"}]'
    $res = Invoke-MerakiApi -Context $ctxA -Path '/organizations'
    Assert-True ($res.StatusCode -eq 200) 'status code must be surfaced'
    Assert-True (@($res.Body).Count -eq 1) 'JSON body must be decoded'
    Assert-True (@($res.Body)[0].id -ceq '810001') 'decoded body content mismatch'
    $orgReqs = Get-Recorded $serverA '/api/v1/organizations'
    Assert-True ($orgReqs.Count -eq 1) 'exactly one request expected'
    Assert-True ($orgReqs[0].Authorization -ceq ('Bearer {0}' -f $ApiKey)) 'documented standard bearer header required on every request'

    # --- Link pagination follows next URLs verbatim -----------------------
    $devPath = '/api/v1/organizations/810001/devices'
    Add-Route $serverA ('GET ' + $devPath) 200 '[{"serial":"Q2KD-0001"},{"serial":"Q2KD-0002"},{"serial":"Q2KD-0003"}]' @{
        Link = ('<{0}/organizations/810001/devices?perPage=3&startingAfter=Q2KD-0003&cursorcheck=p2>; rel=next, <{0}/organizations/810001/devices?perPage=3>; rel=first' -f $serverA.BaseUri)
    }
    Add-Route $serverA ('GET ' + $devPath) 200 '[{"serial":"Q2KD-0004"},{"serial":"Q2KD-0005"}]' @{
        Link = ('<{0}/organizations/810001/devices?perPage=3&startingAfter=Q2KD-0005&cursorcheck=p3>; rel="next"' -f $serverA.BaseUri)
    }
    Add-Route $serverA ('GET ' + $devPath) 200 '[{"serial":"Q2KD-0006"}]'
    $items = Get-MerakiPaged -Context $ctxA -Path '/organizations/810001/devices' -PerPage 3
    Assert-True (@($items).Count -eq 6) ('all pages must be concatenated; got {0}' -f @($items).Count)
    Assert-True ((@($items) | ForEach-Object { $_.serial }) -join ',' -ceq 'Q2KD-0001,Q2KD-0002,Q2KD-0003,Q2KD-0004,Q2KD-0005,Q2KD-0006') 'page order must be preserved'
    $devReqs = Get-Recorded $serverA $devPath
    Assert-True ($devReqs.Count -eq 3) 'three pages means three requests'
    Assert-True ($devReqs[0].Query -ceq '?perPage=3') 'first page must send exactly the perPage parameter'
    Assert-True ($devReqs[1].Query -ceq '?perPage=3&startingAfter=Q2KD-0003&cursorcheck=p2') 'page 2 must use the unquoted rel=next Link URL verbatim (no rebuilt query)'
    Assert-True ($devReqs[2].Query -ceq '?perPage=3&startingAfter=Q2KD-0005&cursorcheck=p3') 'page 3 must use the quoted rel="next" Link URL verbatim'

    # --- Retry-After handling ---------------------------------------------
    $script:Sleeps.Clear()
    $rlPath = '/api/v1/organizations/990001/devices'
    Add-Route $serverA ('GET ' + $rlPath) 429 '{"errors":["API rate limit exceeded for organization"]}' @{ 'Retry-After' = '4' }
    Add-Route $serverA ('GET ' + $rlPath) 200 '[{"serial":"Q2RL-0001"}]'
    $retried = Get-MerakiPaged -Context $ctxA -Path '/organizations/990001/devices'
    Assert-True (@($retried).Count -eq 1) 'the retried page must be returned'
    Assert-True ($script:Sleeps.Count -eq 1) 'exactly one wait for one 429'
    Assert-True ([int]$script:Sleeps[0] -eq 4) 'must wait exactly the Retry-After seconds via the injected SleepAction'
    Assert-True ((Get-Recorded $serverA $rlPath).Count -eq 2) 'one retry request after the 429'

    $script:Sleeps.Clear()
    $ctxLimited = New-MerakiContext -BaseUri $serverA.BaseUri -ApiKey $ApiKey -RetryLimit 1 -SleepAction $sleepAction
    $exPath = '/api/v1/organizations/990002/devices'
    Add-Route $serverA ('GET ' + $exPath) 429 '{"errors":["API rate limit exceeded for organization"]}' @{ 'Retry-After' = '2' }
    Add-Route $serverA ('GET ' + $exPath) 429 '{"errors":["API rate limit exceeded for organization"]}' @{ 'Retry-After' = '2' }
    $threw = $false
    $failMsg = ''
    try { $null = Invoke-MerakiApi -Context $ctxLimited -Path '/organizations/990002/devices' }
    catch { $threw = $true; $failMsg = $_.Exception.Message }
    Assert-True $threw 'persistent 429 past RetryLimit must throw'
    Assert-True ($failMsg -match '429') 'the thrown message must name status 429'
    Assert-True (-not ($failMsg -match [regex]::Escape($ApiKey))) 'the API key must never leak into errors'
    Assert-True ($script:Sleeps.Count -eq 1 -and [int]$script:Sleeps[0] -eq 2) 'RetryLimit=1 means exactly one wait'
    Assert-True ((Get-Recorded $serverA $exPath).Count -eq 2) 'RetryLimit=1 means two requests total'

    # --- redirect trust ---------------------------------------------------
    $ctxRedirect = New-MerakiContext -BaseUri $serverA.BaseUri -ApiKey $ApiKey -TrustedHosts @("127.0.0.1:$($serverB.Port)") -SleepAction $sleepAction
    $trustPath = '/api/v1/organizations/777001/devices'
    Add-Route $serverA ('GET ' + $trustPath) 302 '' @{ Location = ('{0}/organizations/777001/devices' -f $serverB.BaseUri) }
    Add-Route $serverB ('GET ' + $trustPath) 200 '[{"serial":"Q2RD-0001"}]'
    $redirected = Invoke-MerakiApi -Context $ctxRedirect -Path '/organizations/777001/devices'
    Assert-True ($redirected.StatusCode -eq 200) 'trusted redirect must be followed to success'
    Assert-True (@($redirected.Body).Count -eq 1) 'redirected body must be decoded'
    $bReqs = Get-Recorded $serverB $trustPath
    Assert-True ($bReqs.Count -eq 1) 'redirect target must receive the request'
    Assert-True ($bReqs[0].Authorization -ceq ('Bearer {0}' -f $ApiKey)) 'Authorization must be PRESERVED for a trusted Meraki redirect target'

    $untrustPath = '/api/v1/organizations/778002/devices'
    Add-Route $serverA ('GET ' + $untrustPath) 302 '' @{ Location = ('{0}/organizations/778002/devices' -f $serverC.BaseUri) }
    Add-Route $serverC ('GET ' + $untrustPath) 200 '[]'
    $stripped = Invoke-MerakiApi -Context $ctxRedirect -Path '/organizations/778002/devices'
    Assert-True ($stripped.StatusCode -eq 200) 'untrusted redirect is still followed'
    $cReqs = Get-Recorded $serverC $untrustPath
    Assert-True ($cReqs.Count -eq 1) 'untrusted target must receive the request'
    Assert-True ([string]::IsNullOrEmpty($cReqs[0].Authorization)) 'Authorization must be STRIPPED for an untrusted redirect target'

    # --- error envelope ---------------------------------------------------
    Add-Route $serverA 'GET /api/v1/organizations/990404/networks' 404 '{"errors":["Organization not found"]}'
    $threw = $false
    $failMsg = ''
    try { $null = Invoke-MerakiApi -Context $ctxA -Path '/organizations/990404/networks' }
    catch { $threw = $true; $failMsg = $_.Exception.Message }
    Assert-True $threw '404 must throw'
    Assert-True ($failMsg -match '404') 'thrown message must name the status'
    Assert-True ($failMsg -match 'Organization not found') 'documented errors array content must be surfaced'
    Assert-True (-not ($failMsg -match [regex]::Escape($ApiKey))) 'the API key must never leak into errors'

    # --- stable inventory JSON --------------------------------------------
    $netPath = '/api/v1/organizations/810001/networks'
    Add-Route $serverA ('GET ' + $netPath) 200 '[{"id":"N_81011","name":"Depot Wifi","productTypes":["wireless","switch"]},{"id":"N_81005","name":"HQ Appliance","productTypes":["appliance"]}]' @{
        Link = ('<{0}/organizations/810001/networks?perPage=2&startingAfter=N_81011&cursorcheck=nets2>; rel=next' -f $serverA.BaseUri)
    }
    Add-Route $serverA ('GET ' + $netPath) 200 '[{"id":"N_81002","name":"Annex","productTypes":["camera"]}]'
    Add-Route $serverA ('GET ' + $devPath) 200 '[{"serial":"Q2KD-0003-SW03","name":"sw-depot-3","model":"MS250-24","networkId":"N_81011","productType":"switch"},{"serial":"Q2KD-0001-AP01","name":"ap-hq-1","model":"MR46","networkId":"N_81005","productType":"wireless"}]' @{
        Link = ('<{0}/organizations/810001/devices?perPage=2&startingAfter=Q2KD-0001-AP01&cursorcheck=devs2>; rel="next"' -f $serverA.BaseUri)
    }
    Add-Route $serverA ('GET ' + $devPath) 200 '[{"serial":"Q2KD-0002-SW02","name":"sw-depot-2","model":"MS250-24","networkId":"N_81011","productType":"switch"}]'
    $inventory = Get-MerakiInventory -Context $ctxA -OrganizationId '810001' -PerPage 2
    $json = ConvertTo-MerakiInventoryJson -Inventory $inventory
    $expected = '{"organizationId":"810001","networks":[{"id":"N_81002","name":"Annex","productTypes":["camera"]},{"id":"N_81005","name":"HQ Appliance","productTypes":["appliance"]},{"id":"N_81011","name":"Depot Wifi","productTypes":["switch","wireless"]}],"devices":[{"serial":"Q2KD-0001-AP01","name":"ap-hq-1","model":"MR46","networkId":"N_81005","productType":"wireless"},{"serial":"Q2KD-0002-SW02","name":"sw-depot-2","model":"MS250-24","networkId":"N_81011","productType":"switch"},{"serial":"Q2KD-0003-SW03","name":"sw-depot-3","model":"MS250-24","networkId":"N_81011","productType":"switch"}]}'
    Assert-True ($json -ceq $expected) ("inventory JSON must be byte-stable with ordinal sorting and pinned key order; got: {0}" -f $json)
    $again = ConvertTo-MerakiInventoryJson -Inventory $inventory
    Assert-True ($again -ceq $expected) 'inventory JSON must be deterministic across calls'
    $netReqs = Get-Recorded $serverA $netPath
    Assert-True ($netReqs.Count -eq 2) 'network pages must both be fetched'
    Assert-True ($netReqs[0].Query -ceq '?perPage=2') 'inventory must pass PerPage through to the first page'
    Assert-True ($netReqs[1].Query -ceq '?perPage=2&startingAfter=N_81011&cursorcheck=nets2') 'inventory network paging must follow Link URLs verbatim'

    Write-Output ("OK ({0} checks)" -f $script:Checks)
    exit 0
}
catch {
    Write-Output ("FAILED: {0}" -f $_)
    exit 1
}
finally {
    foreach ($server in @($serverA, $serverB, $serverC)) {
        if ($null -ne $server) { Stop-FakeServer $server }
    }
}
