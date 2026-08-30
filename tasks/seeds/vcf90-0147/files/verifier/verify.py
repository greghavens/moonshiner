#!/usr/bin/env python3
"""Run local syntax and build checks for the implementation."""

import os
import shutil
import subprocess
from pathlib import Path

root = Path.cwd()
skip = {"tests", "test", "verify", "verifier", "verification", "harness", "mock", "mocks", "grader", "grader_tests", "protected_tests", "test_support", ".git"}
sources = [p for suffix in ("*.ps1", "*.psm1")
           for p in root.rglob(suffix)
           if p.is_file() and not (set(p.relative_to(root).parts[:-1]) & skip)]
pwsh = shutil.which("pwsh")
if not pwsh or not sources:
    raise SystemExit("PowerShell or implementation sources not found")
command = ("$null=$tokens=$errors=$null;"
           "[System.Management.Automation.Language.Parser]::ParseFile("
           "$env:VCF_LOCAL_CHECK_PATH,[ref]$tokens,[ref]$errors)>$null;"
           "if($errors.Count){$errors|ForEach-Object{Write-Error $_};exit 1}")
for source in sources:
    environment = os.environ.copy()
    environment["VCF_LOCAL_CHECK_PATH"] = str(source)
    subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                   check=True, env=environment)
print(f"local checks passed ({len(sources)} PowerShell source files)")
