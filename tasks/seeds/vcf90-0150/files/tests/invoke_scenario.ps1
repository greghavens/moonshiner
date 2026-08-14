param(
    [Parameter(Mandatory)]
    [string] $ModulePath,

    [Parameter(Mandatory)]
    [uri] $ServerUri
)

$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force

$leaseInputs = [ordered]@{
    'Lease Expiration Date' = '2026-09-30T00:00:00Z'
}

$failedResult = Invoke-VcfAutomationDeploymentChange `
    -ServerUri $ServerUri `
    -AccessToken 'fixture-token' `
    -DeploymentId '11111111-1111-4111-8111-111111111111' `
    -Name 'payments-prod-renamed' `
    -ActionId 'Deployment.ChangeLease' `
    -Inputs $leaseInputs

$ownerInputs = [ordered]@{
    Owner = 'fixture-owner'
}

$successfulResult = Invoke-VcfAutomationDeploymentChange `
    -ServerUri $ServerUri `
    -AccessToken 'fixture-token' `
    -DeploymentId '33333333-3333-4333-8333-333333333333' `
    -Name 'payments-dev-renamed' `
    -Description '' `
    -IconId '44444444-4444-4444-8444-444444444444' `
    -ActionId 'Deployment.ChangeOwner' `
    -Inputs $ownerInputs `
    -Reason ''

@($failedResult, $successfulResult) | ConvertTo-Json -Depth 12 -Compress
