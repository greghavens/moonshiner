#!/usr/bin/env python3
"""Run local syntax and build checks for the implementation."""

from __future__ import annotations

import py_compile
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path.cwd()
SKIP = {".git", "tests", "test", "verify", "verifier", "verification",
        "harness", "mock", "mocks", "grader", "grader_tests",
        "protected_tests", "test_support"}


def candidates(suffix: str):
    for path in ROOT.rglob(f"*{suffix}"):
        if path.is_file() and not (set(path.relative_to(ROOT).parts[:-1]) & SKIP):
            yield path


checked = 0
for path in candidates(".py"):
    py_compile.compile(str(path), doraise=True)
    checked += 1

shell = shutil.which("bash")
if shell:
    for path in candidates(".sh"):
        subprocess.run([shell, "-n", str(path)], check=True)
        checked += 1

pwsh = shutil.which("pwsh")
if pwsh:
    for path in [*candidates(".ps1"), *candidates(".psm1")]:
        command = ("$null=$tokens=$errors=$null; "
                   "[System.Management.Automation.Language.Parser]::ParseFile("
                   "$env:VCF_LOCAL_CHECK_PATH,[ref]$tokens,[ref]$errors)>$null; "
                   "if($errors.Count){$errors|ForEach-Object{Write-Error $_};exit 1}")
        environment = os.environ.copy()
        environment["VCF_LOCAL_CHECK_PATH"] = str(path)
        subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command",
                        command], check=True, env=environment)
        checked += 1

go = shutil.which("go")
if go and (ROOT / "go.mod").is_file():
    subprocess.run([go, "test", "./..."], cwd=ROOT, check=True)
    checked += 1

javac = shutil.which("javac")
java_sources = list(candidates(".java"))
if javac and java_sources:
    with tempfile.TemporaryDirectory() as output:
        subprocess.run([javac, "-d", output, *map(str, java_sources)],
                       cwd=ROOT, check=True)
    checked += len(java_sources)

if not checked:
    raise SystemExit("no implementation source files found")

print(f"local checks passed ({checked} source files)")
