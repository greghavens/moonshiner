Set-StrictMode -Version Latest

function Invoke-VcfaJsonRequest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Uri,

        [Parameter(Mandatory)]
        [ValidateSet('GET', 'PATCH', 'POST')]
        [string] $Method,

        [Parameter(Mandatory)]
        [string] $AccessToken,

        [AllowNull()]
        [object] $Body
    )

    $invokeParameters = @{
        Uri = $Uri
        Method = $Method
        Headers = @{
            Authorization = "Bearer $AccessToken"
            Accept = 'application/json'
        }
        StatusCodeVariable = 'responseStatusCode'
    }

    if ($null -ne $Body) {
        $invokeParameters.ContentType = 'application/json'
        $invokeParameters.Body = $Body | ConvertTo-Json -Depth 16 -Compress
    }

    $response = Invoke-RestMethod @invokeParameters
    [pscustomobject]@{
        StatusCode = [int] $responseStatusCode
        Body = $response
    }
}

function Invoke-VcfAutomationDeploymentChange {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $ServerUri,

        [Parameter(Mandatory)]
        [string] $AccessToken,

        [Parameter(Mandatory)]
        [string] $DeploymentId,

        [Parameter(Mandatory)]
        [ValidateLength(1, 900)]
        [string] $Name,

        [Parameter()]
        [ValidateLength(0, 2000)]
        [string] $Description,

        [Parameter()]
        [string] $IconId,

        [Parameter(Mandatory)]
        [string] $ActionId,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary] $Inputs,

        [Parameter()]
        [string] $Reason
    )

    $root = $ServerUri.AbsoluteUri.TrimEnd('/')
    $escapedDeploymentId = [uri]::EscapeDataString($DeploymentId)

    # BUG: Optional fields are serialized even when the caller never supplied
    # them, producing empty strings on the wire.
    $deploymentUpdate = [ordered]@{
        name = $Name
        description = $Description
        iconId = $IconId
    }
    $patch = Invoke-VcfaJsonRequest -Uri "$root/deployment/api/deployments/$escapedDeploymentId" `
        -Method PATCH -AccessToken $AccessToken -Body $deploymentUpdate

    $actionRequest = [ordered]@{
        actionId = $ActionId
        inputs = $Inputs
        reason = $Reason
    }
    $submitted = Invoke-VcfaJsonRequest -Uri "$root/deployment/api/deployments/$escapedDeploymentId/requests" `
        -Method POST -AccessToken $AccessToken -Body $actionRequest

    $requestId = [string] $submitted.Body.id
    $escapedRequestId = [uri]::EscapeDataString($requestId)
    $request = Invoke-VcfaJsonRequest -Uri "$root/deployment/api/requests/$escapedRequestId" `
        -Method GET -AccessToken $AccessToken -Body $null

    # BUG: The later business failure becomes an exception, so callers lose
    # the fact that the preceding PATCH and POST both succeeded.
    if ([string] $request.Body.status -eq 'FAILED') {
        throw "VCF Automation request $requestId failed: $($request.Body.details)"
    }

    [pscustomobject]@{
        DeploymentId = $DeploymentId
        RequestId = $requestId
        Succeeded = $true
        OverallStatus = 'Succeeded'
        Steps = @(
            [pscustomobject]@{
                Operation = 'Patch Deployment'
                State = 'Succeeded'
                HttpStatus = $patch.StatusCode
                RemoteStatus = [string] $patch.Body.status
                Details = $null
            }
            [pscustomobject]@{
                Operation = 'Submit Deployment Action Request'
                State = 'Succeeded'
                HttpStatus = $submitted.StatusCode
                RemoteStatus = [string] $submitted.Body.status
                Details = $null
            }
            [pscustomobject]@{
                Operation = 'Get Request'
                State = 'Succeeded'
                HttpStatus = $request.StatusCode
                RemoteStatus = [string] $request.Body.status
                Details = $request.Body.details
            }
        )
    }
}

Export-ModuleMember -Function Invoke-VcfAutomationDeploymentChange
