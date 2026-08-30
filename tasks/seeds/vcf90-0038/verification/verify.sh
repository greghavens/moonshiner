#!/usr/bin/env bash
# PROTECTED FILE -- do not modify.
#
# Confirms the protected files are untouched, checks the prerequisites, then
# runs the acceptance suite. The suite talks to a loopback fixture only: no
# VMware endpoint is contacted.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
PWSH="${PWSH:-pwsh}"

echo "== checking protected file integrity =="
"$PYTHON" -I - <<'PY'
import hashlib
import sys

failures = []
with open("PROTECTED.sha256", "r", encoding="utf-8") as manifest:
    for line in manifest:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        expected, path = line.split(None, 1)
        path = path.lstrip("*").strip()
        try:
            with open(path, "rb") as handle:
                actual = hashlib.sha256(handle.read()).hexdigest()
        except OSError as exc:
            failures.append("{0}: {1}".format(path, exc))
            continue
        if actual != expected:
            failures.append("{0}: content changed".format(path))
        else:
            print("  ok  {0}".format(path))

if failures:
    print("\nprotected files were modified or removed:", file=sys.stderr)
    for failure in failures:
        print("  " + failure, file=sys.stderr)
    raise SystemExit(1)
PY

echo
echo "== checking prerequisites =="
if ! command -v "$PWSH" >/dev/null 2>&1; then
    echo "PowerShell ($PWSH) is not on PATH." >&2
    exit 1
fi
echo "  ok  $("$PWSH" -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()')"

if ! "$PWSH" -NoProfile -NonInteractive -Command \
        'if (-not (Get-Module -ListAvailable -Name "VMware.Sdk.Vcf.*")) { exit 1 }' \
        >/dev/null 2>&1; then
    cat >&2 <<'MSG'
The VMware.Sdk.Vcf PowerCLI modules are not installed.

They are an environment prerequisite for this task and are never vendored into
the repository. Install them with:

    pwsh -NoProfile -Command 'Install-Module VMware.Sdk.Vcf.SddcManager -Scope CurrentUser -Force'
MSG
    exit 1
fi
"$PWSH" -NoProfile -NonInteractive -Command \
    'Get-Module -ListAvailable -Name "VMware.Sdk.Vcf.*" |
        Select-Object -Unique Name,Version |
        ForEach-Object { "  ok  " + $_.Name + " " + $_.Version }'

echo
echo "== running acceptance tests =="
"$PYTHON" - <<'PY'
import os
import sys
import unittest

sys.path.insert(0, os.getcwd())
suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
PY
