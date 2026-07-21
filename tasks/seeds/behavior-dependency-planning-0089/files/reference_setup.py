#!/usr/bin/env python3
"""Run the patch-only reference solution when it is present."""

from pathlib import Path
import subprocess


solution = Path("reference_solution.sh")
if solution.is_file():
    subprocess.run(["bash", str(solution)], check=True)
