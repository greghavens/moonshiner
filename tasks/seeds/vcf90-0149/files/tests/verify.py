#!/usr/bin/env python3
"""Run local syntax and build checks for the implementation."""

import py_compile
from pathlib import Path
root = Path.cwd()
sources = [p for p in root.rglob("*.py") if p.is_file()]
if not sources:
    raise SystemExit("no implementation sources found")
for source in sources:
    py_compile.compile(str(source), doraise=True)
print("local checks passed")
