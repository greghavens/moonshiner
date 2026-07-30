Set-StrictMode -Version Latest

function Get-VksMember {
    param(
        [AllowNull()]
        $InputObject,

        [Parameter(Mandatory)]
        [string] $Name
    )

    if ($null -ne $InputObject) {
        $Property = @(
            $InputObject.PSObject.Properties |
                Where-Object { $_.Name -ceq $Name }
        )
        if ($Property.Count -eq 1) {
            return [pscustomobject]@{
                Found = $true
                Value = $Property[0].Value
            }
        }
    }
    return [pscustomobject]@{
        Found = $false
        Value = $null
    }
}

function Assert-VksSafeToken {
    param(
        [Parameter(Mandatory)]
        [string] $Token
    )

    if (
        [string]::IsNullOrWhiteSpace($Token) -or
        $Token.IndexOf("`r", [StringComparison]::Ordinal) -ge 0 -or
        $Token.IndexOf("`n", [StringComparison]::Ordinal) -ge 0
    ) {
        throw [ArgumentException]::new(
            'KubernetesToken must be nonblank and safe for an HTTP header.'
        )
    }
}

function Resolve-VksApiOrigin {
    param(
        [Parameter(Mandatory)]
        [string] $MasterHost,

        [Parameter(Mandatory)]
        [ValidateSet('http', 'https')]
        [string] $Scheme
    )

    if (
        [string]::IsNullOrWhiteSpace($MasterHost) -or
        $MasterHost.Contains('/') -or
        $MasterHost.Contains('?') -or
        $MasterHost.Contains('#') -or
        $MasterHost.Contains('@')
    ) {
        throw [InvalidDataException]::new(
            'Namespace discovery returned an invalid master_host.'
        )
    }

    $Origin = $null
    if (
        -not [uri]::TryCreate(
            "${Scheme}://${MasterHost}/",
            [UriKind]::Absolute,
            [ref] $Origin
        ) -or
        [string]::IsNullOrWhiteSpace($Origin.Host) -or
        -not [string]::IsNullOrEmpty($Origin.UserInfo) -or
        -not [string]::IsNullOrEmpty($Origin.Query) -or
        -not [string]::IsNullOrEmpty($Origin.Fragment) -or
        $Origin.AbsolutePath -cne '/'
    ) {
        throw [InvalidDataException]::new(
            'Namespace discovery returned an invalid master_host.'
        )
    }
    return $Origin
}

function Invoke-VksJsonGet {
    param(
        [Parameter(Mandatory)]
        [Net.Http.HttpClient] $HttpClient,

        [Parameter(Mandatory)]
        [uri] $Uri
    )

    $Request = [Net.Http.HttpRequestMessage]::new(
        [Net.Http.HttpMethod]::Get,
        $Uri
    )
    $Request.Headers.Accept.Add(
        [Net.Http.Headers.MediaTypeWithQualityHeaderValue]::new(
            'application/json'
        )
    )

    $Response = $null
    try {
        try {
            $Response = $HttpClient.SendAsync(
                $Request
            ).GetAwaiter().GetResult()
        }
        catch {
            throw [IOException]::new(
                'VKS Cluster API transport failed.'
            )
        }
        if (-not $Response.IsSuccessStatusCode) {
            throw [IOException]::new(
                'VKS Cluster API request failed with HTTP {0}.' -f
                [int] $Response.StatusCode
            )
        }
        $Text = $Response.Content.ReadAsStringAsync(
        ).GetAwaiter().GetResult()
        if ([string]::IsNullOrWhiteSpace($Text)) {
            throw [InvalidDataException]::new(
                'VKS Cluster API returned an empty success response.'
            )
        }
        try {
            return ($Text | ConvertFrom-Json -Depth 100 -ErrorAction Stop)
        }
        catch {
            throw [InvalidDataException]::new(
                'VKS Cluster API returned malformed JSON.'
            )
        }
    }
    finally {
        if ($null -ne $Response) {
            $Response.Dispose()
        }
        $Request.Dispose()
    }
}

function Get-VcfVksClusterInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [VMware.Bindings.vSphere.Api.IVcenterNamespacesUserInstancesApi]
        $NamespaceApi,

        [Parameter(Mandatory)]
        [string] $Namespace,

        [Parameter(Mandatory)]
        [string] $KubernetesToken,

        [ValidateRange(1, [int]::MaxValue)]
        [int] $PageSize = 200,

        [ValidateSet('http', 'https')]
        [string] $KubernetesScheme = 'https',

        [switch] $SkipCertificateCheck
    )

    throw [NotImplementedException]::new(
        'TODO: discover the namespace endpoint and collect every VKS cluster page.'
    )
}

Export-ModuleMember -Function 'Get-VcfVksClusterInventory'

