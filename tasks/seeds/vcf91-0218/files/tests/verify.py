#!/usr/bin/env python3
"""Run local syntax and build checks for the implementation."""

import shutil
import subprocess
import tempfile
from pathlib import Path

root = Path.cwd()
skip = {"tests", "test", "verify", "verifier", "verification", "harness", "mock", "mocks", "grader", "grader_tests", "protected_tests", "test_support", ".git"}
sources = [str(p) for p in root.rglob("*.java")
           if p.is_file() and not (set(p.relative_to(root).parts[:-1]) & skip)]
compiler = shutil.which("javac")
if not compiler or not sources:
    raise SystemExit("Java compiler or implementation sources not found")
with tempfile.TemporaryDirectory() as output:
    subprocess.run([compiler, "-d", output, *sources], cwd=root, check=True)
print(f"local checks passed ({len(sources)} Java source files)")
