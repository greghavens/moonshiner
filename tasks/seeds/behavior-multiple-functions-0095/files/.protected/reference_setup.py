#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine registry executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".clinic" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
MARKER_PATH = ROOT / ".reference_solution"
REPORT_PATH = ROOT / "availability_check.json"
MARKER_DIGEST = "3f56fe1d8f56934a95c578213c1f77bce6e73a2fc6c914b4ad3a4584d961b69b"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "clinic.sqlite3",
        REGISTRY_RUNTIME / "clinic.sqlite3-journal",
        REGISTRY_RUNTIME / "clinic.sqlite3-shm",
        REGISTRY_RUNTIME / "clinic.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "clinic-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_executable(arguments: list[str]) -> str:
    result = subprocess.run(
        ["./clinic-registry", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"reference registry operation failed: {result.stderr.strip()}"
        )
    return result.stdout


def main() -> None:
    if (
        not MARKER_PATH.is_file()
        or hashlib.sha256(MARKER_PATH.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    run_executable(["--help"])
    output = run_executable(
        [
            "availability",
            "--service",
            "Annual wellness visit",
            "--location",
            "Dale Clinic",
            "--date",
            "2026-11-20",
        ]
    )
    payload = json.loads(output)
    availability = payload.get("availability")
    if not isinstance(availability, list) or len(availability) != 1:
        raise RuntimeError("reference availability was not uniquely resolved")
    result = availability[0]
    if (
        not isinstance(result, dict)
        or result.get("service") != "Annual wellness visit"
        or result.get("location") != "Dale Clinic"
        or result.get("date") != "2026-11-20"
    ):
        raise RuntimeError("reference availability returned an out-of-scope result")
    if not REPORT_PATH.is_file() or REPORT_PATH.read_text(encoding="utf-8") != output:
        raise RuntimeError("reference patch is not grounded in registry output")


if __name__ == "__main__":
    main()
