Set-StrictMode -Version Latest

function Set-VcfLogForwarders {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Server,

        [Parameter(Mandatory)]
        [pscredential] $Credential,

        [Parameter(Mandatory, ValueFromPipeline)]
        [object[]] $Forwarder,

        [ValidateSet('Local', 'ActiveDirectory', 'vIDM')]
        [string] $Provider = 'Local',

        [switch] $ShowDetails
    )

    begin {
        $items = [System.Collections.Generic.List[object]]::new()
    }

    process {
        foreach ($item in $Forwarder) {
            $items.Add($item)
        }
    }

    end {
        throw 'Set-VcfLogForwarders has not been implemented.'
    }
}

Export-ModuleMember -Function Set-VcfLogForwarders
