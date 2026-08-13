<#
.SYNOPSIS
    Writes a fresh self-signed loopback certificate for the contract mock.
.DESCRIPTION
    The mock listens on https because the vSphere Automation API is an https API. The key
    pair is generated on every run so nothing expirable is checked into the repository.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $CertificatePath,
    [Parameter(Mandatory)][string] $KeyPath
)

$ErrorActionPreference = 'Stop'

$rsa = [System.Security.Cryptography.RSA]::Create(2048)
try {
    $request = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
        [System.Security.Cryptography.X509Certificates.X500DistinguishedName]::new('CN=localhost'),
        $rsa,
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)

    $sanBuilder = [System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
    $sanBuilder.AddDnsName('localhost')
    $sanBuilder.AddIpAddress([System.Net.IPAddress]::Loopback)
    $request.CertificateExtensions.Add($sanBuilder.Build())

    $now = [System.DateTimeOffset]::UtcNow
    $certificate = $request.CreateSelfSigned($now.AddDays(-1), $now.AddDays(365))

    $pem = "-----BEGIN CERTIFICATE-----`n" +
        [Convert]::ToBase64String($certificate.RawData, 'InsertLineBreaks') +
        "`n-----END CERTIFICATE-----`n"
    $keyPem = "-----BEGIN PRIVATE KEY-----`n" +
        [Convert]::ToBase64String($rsa.ExportPkcs8PrivateKey(), 'InsertLineBreaks') +
        "`n-----END PRIVATE KEY-----`n"

    [System.IO.File]::WriteAllText($CertificatePath, $pem)
    [System.IO.File]::WriteAllText($KeyPath, $keyPem)
}
finally {
    $rsa.Dispose()
}
