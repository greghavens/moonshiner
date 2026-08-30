$ErrorActionPreference = 'Stop'
$skip = '[\\/](tests|test|verify|verifier|verification|harness|mock|mocks|grader|grader_tests|protected_tests|test_support)[\\/]'
$files = Get-ChildItem -Path . -Recurse -File -Include *.ps1,*.psm1 |
    Where-Object { $_.FullName -ne $PSCommandPath -and $_.FullName -notmatch $skip }
if (-not $files) { throw 'No PowerShell implementation sources found.' }
foreach ($file in $files) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $file.FullName, [ref]$tokens, [ref]$errors) > $null
    if ($errors.Count) { throw ($errors | Out-String) }
}
Write-Host "Local checks passed ($($files.Count) PowerShell source files)."
