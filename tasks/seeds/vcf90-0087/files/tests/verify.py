#!/usr/bin/env python3
"""Run local syntax and build checks for the implementation."""

import py_compile
from pathlib import Path

root = Path.cwd()
skip = {"tests", "test", "verify", "verifier", "verification", "harness", "mock", "mocks", "grader", "grader_tests", "protected_tests", "test_support", ".git"}
sources = [p for p in root.rglob("*.py")
           if p.is_file() and not (set(p.relative_to(root).parts[:-1]) & skip)]
if not sources:
    raise SystemExit("no Python implementation sources found")
for source in sources:
    py_compile.compile(str(source), doraise=True)
print(f"local checks passed ({len(sources)} Python source files)")
