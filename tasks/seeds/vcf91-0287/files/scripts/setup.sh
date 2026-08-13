#!/usr/bin/env bash
# Checks the runtime prerequisites supplied by the trace harness.
set -euo pipefail

command -v python3 >/dev/null
command -v pwsh >/dev/null
pwsh -NoProfile -NonInteractive -Command \
  'if ($PSVersionTable.PSVersion -lt [version]"7.2") { throw "PowerShell 7.2 or newer is required" }'

echo "prerequisites ready"
