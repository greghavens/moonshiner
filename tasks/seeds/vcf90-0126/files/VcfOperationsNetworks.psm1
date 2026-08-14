Set-StrictMode -Version Latest

function Invoke-VcfOperationsNetworksCertificateUpdate {
    <#
    .SYNOPSIS
    Replaces a VCF Operations for Networks certificate and waits for completion.

    .PARAMETER Server
    The VCF Operations for Networks authority root, for example https://ops.example.test.

    .PARAMETER Token
    A VCF Operations for Networks API token, without the NetworkInsight scheme.

    .PARAMETER CertificateId
    The certificate identifier used by the updateCertificate operation.

    .PARAMETER Certificate
    The PEM-encoded replacement certificate.

    .PARAMETER PrivateKey
    The PEM-encoded replacement private key.

    .PARAMETER Chain
    The optional PEM-encoded trusted root CA chain. It must be absent from the JSON
    request when this parameter is not supplied.

    .PARAMETER PollIntervalMilliseconds
    Milliseconds to wait between nonterminal status responses. Defaults to 250.

    .OUTPUTS
    The terminal CertificateUpdateStatus object when its status is SUCCESS. Throws
    when the terminal status is FAILED.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Server,

        [Parameter(Mandatory)]
        [string] $Token,

        [Parameter(Mandatory)]
        [string] $CertificateId,

        [Parameter(Mandatory)]
        [string] $Certificate,

        [Parameter(Mandatory)]
        [string] $PrivateKey,

        [Parameter()]
        [AllowEmptyString()]
        [string] $Chain,

        [Parameter()]
        [ValidateRange(0, [int]::MaxValue)]
        [int] $PollIntervalMilliseconds = 250
    )

    throw 'Invoke-VcfOperationsNetworksCertificateUpdate is not implemented.'
}

Export-ModuleMember -Function Invoke-VcfOperationsNetworksCertificateUpdate
