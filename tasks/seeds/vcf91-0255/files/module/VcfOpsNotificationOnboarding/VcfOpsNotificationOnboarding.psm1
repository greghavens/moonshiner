Set-StrictMode -Version Latest

<#
    VMware Cloud Foundation Operations 9.1 - outbound notification onboarding.

    The service operations this module drives are projected in docs/contract.json
    from the pinned OpenAPI specification recorded in docs/official_sources.json:

      acquireToken                  POST /suite-api/api/auth/token/acquire
      getCurrentVersionOfServer     GET  /suite-api/api/versions/current
      getAlertPluginTypes           GET  /suite-api/api/alertplugins/types
      createAlertPlugin             POST /suite-api/api/alertplugins
      getNotificationTemplates      GET  /suite-api/api/notifications/templates
      createNotificationPluginRule  POST /suite-api/api/notifications/rules

    Requests are issued with the VMware.Sdk.Vcf.Ops PowerCLI cmdlets, which the
    environment installs as a prerequisite. Connect-VcfOpsServer performs
    acquireToken and getCurrentVersionOfServer; each remaining operation has an
    Invoke-VcfOps<OperationId> cmdlet, and request models are built with the
    matching Initialize-VcfOps<schema> cmdlet, for example:

      $value  = Initialize-VcfOpsnamevalue -Name 'SMTP_HOST' -Value 'smtp.example.com'
      $plugin = Initialize-VcfOpsnotificationplugin -Name 'x' -PluginTypeId 'y' -ConfigValues @($value)
      $result = Invoke-VcfOpsCreateAlertPlugin -Server $connection -NotificationPlugin $plugin

    A request model serializes only the properties that were actually supplied,
    so an optional field is omitted from the wire body by leaving its parameter
    unbound rather than by passing an empty or default value.

    A non-success HTTP status surfaces as a terminating error whose
    InnerException is a VMware.Binding.OpenApi.Client.ApiException carrying an
    ErrorCode (the HTTP status) and an ErrorContent (the raw response body).
#>

function Connect-VcfOpsNotificationSession {
    <#
    .SYNOPSIS
        Opens an authenticated VCF Operations session.
    .DESCRIPTION
        Acquires a token with acquireToken and returns the resulting connection,
        which every operation in this module is issued against.
    #>
    [CmdletBinding()]
    [OutputType([psobject])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [pscredential] $Credential,

        [Parameter()]
        [ValidateRange(1, 65535)]
        [int] $Port = 443,

        [Parameter()]
        [ValidateSet('http', 'https')]
        [string] $Protocol = 'https',

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $AuthSource,

        [Parameter()]
        [switch] $SkipCertificateCheck
    )

    throw [System.NotImplementedException]::new('Connect-VcfOpsNotificationSession is not implemented.')
}

function New-VcfOpsNotificationBinding {
    <#
    .SYNOPSIS
        Onboards an outbound alert plugin instance and binds a notification
        rule to it.
    .DESCRIPTION
        Applies the change in ordered steps - VerifyPluginType, CreatePlugin,
        ResolveTemplate, CreateRule - and returns a report describing exactly
        which of them were applied.
    #>
    [CmdletBinding()]
    [OutputType([psobject])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [psobject] $Connection,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $PluginName,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $PluginTypeId,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [System.Collections.IDictionary] $ConfigValue,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $RuleName,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $PluginDescription,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $TemplateName,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $CollectorGroupId,

        [Parameter()]
        [ValidateSet('UNKNOWN', 'NONE', 'INFORMATION', 'WARNING', 'IMMEDIATE', 'CRITICAL', 'AUTO')]
        [string[]] $Criticality
    )

    throw [System.NotImplementedException]::new('New-VcfOpsNotificationBinding is not implemented.')
}

Export-ModuleMember -Function 'Connect-VcfOpsNotificationSession', 'New-VcfOpsNotificationBinding'
