#!/usr/bin/env python3
"""Run local syntax and build checks for the implementation."""

import shutil
import subprocess
from pathlib import Path

root = Path.cwd()
go = shutil.which("go")
if not go or not (root / "go.mod").is_file():
    raise SystemExit("Go toolchain or go.mod not found")
subprocess.run([go, "test", "./..."], cwd=root, check=True)
print("local Go checks passed")
