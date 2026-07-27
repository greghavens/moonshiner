#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine expenses executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EXPENSE_RUNTIME = ROOT / ".expenses" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
MARKER_PATH = ROOT / ".reference_solution"
REPORT_PATH = ROOT / "expense_preferences.md"
MARKER_DIGEST = "a3873aa35fdf7a4900e65ac0572c7537244ecd8a9ae12e6b37ba0a6484772bc2"


def reset_generated_state() -> None:
    for path in (
        EXPENSE_RUNTIME / "expenses.sqlite3",
        EXPENSE_RUNTIME / "expenses.sqlite3-journal",
        EXPENSE_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "expense-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_executable(arguments: list[str]) -> str:
    result = subprocess.run(
        ["./expense-desk", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"reference expenses operation failed: {result.stderr.strip()}"
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
    payload = json.loads(run_executable(["profile"]))
    requested_fields = payload.get("requested_fields")
    preferences = payload.get("preferences")
    if (
        not isinstance(requested_fields, list)
        or not requested_fields
        or not all(isinstance(field, str) for field in requested_fields)
        or len(set(requested_fields)) != len(requested_fields)
        or not isinstance(preferences, dict)
        or not all(
            isinstance(field, str) and isinstance(value, str)
            for field, value in preferences.items()
        )
    ):
        raise RuntimeError("reference profile result is incomplete")
    if set(preferences) - set(requested_fields):
        raise RuntimeError("reference profile returned an unidentified field")
    if set(requested_fields).issubset(preferences):
        raise RuntimeError("reference profile did not exercise unknown fallback")
    expected_report = "".join(
        f"- {field}: {preferences.get(field, 'unknown')}\n"
        for field in requested_fields
    )
    if (
        not REPORT_PATH.is_file()
        or REPORT_PATH.read_text(encoding="utf-8") != expected_report
    ):
        raise RuntimeError(
            "reference patch is not grounded in the expenses profile result"
        )


if __name__ == "__main__":
    main()
