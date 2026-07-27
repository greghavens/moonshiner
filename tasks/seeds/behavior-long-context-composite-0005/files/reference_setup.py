#!/usr/bin/env python3
"""No-op at task baseline; run the reference execution after its patch applies."""

from pathlib import Path
import subprocess
import sys


solution = Path(__file__).resolve().parent / ".reference_solution.py"
if solution.is_file():
    subprocess.run([sys.executable, str(solution)], check=True)
