Set-StrictMode -Version Latest

function New-VcfVcenterTpmEvidenceClient {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Server,

        [scriptblock] $OperationInvoker
    )

    throw [NotImplementedException]::new(
        'TODO: create the caller-owned vCenter TPM evidence client.'
    )
}

function Get-VcfHostTpmFailureEvidence {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Client,

        [Parameter(Mandatory)]
        [string] $HostId,

        [Parameter(Mandatory)]
        [string] $TpmId
    )

    throw [NotImplementedException]::new(
        'TODO: collect the configured TPM and its event log before diagnosing.'
    )
}

Export-ModuleMember -Function @(
    'New-VcfVcenterTpmEvidenceClient'
    'Get-VcfHostTpmFailureEvidence'
)
