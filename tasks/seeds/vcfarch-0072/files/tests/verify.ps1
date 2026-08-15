Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    & python3 tests/verify_architecture.py
    if ($LASTEXITCODE -ne 0) {
        throw "architecture verification failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
