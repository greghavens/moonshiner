Set-StrictMode -Version Latest

function Invoke-VcfCredentialRotation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        $Server,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $ResourceType,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $ResourceName,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Username,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $CredentialType,

        [Parameter()]
        [string] $AccountType,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [scriptblock] $DrainAction,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [scriptblock] $PublishAction,

        [Parameter()]
        [int] $DrainLimit = 10,

        [Parameter()]
        [int] $DrainIntervalSeconds = 2,

        [Parameter()]
        [int] $PollLimit = 60,

        [Parameter()]
        [int] $PollIntervalSeconds = 5,

        [Parameter()]
        [scriptblock] $SleepAction
    )

    throw [NotImplementedException]::new(
        'TODO: drain the old secret, rotate the credential, and publish only a confirmed replacement.'
    )
}

Export-ModuleMember -Function 'Invoke-VcfCredentialRotation'
