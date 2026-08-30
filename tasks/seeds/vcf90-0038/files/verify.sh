#!/bin/sh
set -eu
files="$(find . -type f \( -name '*.ps1' -o -name '*.psm1' \) ! -path './tests/*' ! -path './mock/*' -print)"
test -n "$files"
for file in $files; do
  VCF_LOCAL_CHECK_PATH="$file" pwsh -NoProfile -NonInteractive -Command '$null=$tokens=$errors=$null;[System.Management.Automation.Language.Parser]::ParseFile($env:VCF_LOCAL_CHECK_PATH,[ref]$tokens,[ref]$errors)>$null;if($errors.Count){$errors|ForEach-Object{Write-Error $_};exit 1}'
done
