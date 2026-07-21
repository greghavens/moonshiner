Set-StrictMode -Version Latest

function Invoke-ProfiledPipeline {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, ValueFromPipeline)]
        [AllowNull()]
        [object] $InputObject,

        [Parameter(Mandatory)]
        [scriptblock] $Transform,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary] $OperationCounter
    )

    begin {
        if (-not $OperationCounter.Contains('StorageOperations')) {
            $OperationCounter['StorageOperations'] = [long] 0
        }

        $results = @()
    }

    process {
        $transformed = & $Transform $InputObject

        foreach ($item in $transformed) {
            # Array concatenation allocates a new buffer and writes every element.
            $OperationCounter['StorageOperations'] =
                [long] $OperationCounter['StorageOperations'] + $results.Count + 1
            $results += $item
        }
    }

    end {
        $results
    }
}

Export-ModuleMember -Function Invoke-ProfiledPipeline
