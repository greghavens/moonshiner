#!/usr/bin/env python3
"""Run reference-only executable proof when the reference patch is applied."""

from pathlib import Path
import runpy


plan = Path(__file__).resolve().parent / ".reference_plan.py"
if plan.exists():
    runpy.run_path(str(plan), run_name="__main__")
else:
    print("reference plan absent; baseline environment is ready")
