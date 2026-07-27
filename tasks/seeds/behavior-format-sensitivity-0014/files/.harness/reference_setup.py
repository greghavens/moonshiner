#!/usr/bin/env python3
"""Exercise the reference deliverable through the genuine expense executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DELIVERABLE_PATH = ROOT / "envelope.xml"
DELIVERABLE_DIGEST = "c4d10b821657c3c45b90a62727c72816360eae5c1a3acda28f759ca26c11bf77"
RUNTIME_FILES = (
    ROOT / ".expenses" / "runtime" / "expenses.sqlite3",
    ROOT / ".expenses" / "runtime" / "expenses.sqlite3-shm",
    ROOT / ".expenses" / "runtime" / "expenses.sqlite3-wal",
    ROOT / ".expenses" / "runtime" / "initialize.lock",
    ROOT / ".harness" / "runtime" / "expenses-audit.jsonl",
)


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> None:
    if (
        not DELIVERABLE_PATH.is_file()
        or hashlib.sha256(DELIVERABLE_PATH.read_bytes()).hexdigest()
        != DELIVERABLE_DIGEST
    ):
        return
    for path in RUNTIME_FILES:
        path.unlink(missing_ok=True)

    help_result = run(["./expensesctl", "--help"])
    if help_result.returncode != 0:
        raise SystemExit(
            "reference expense help failed: " + help_result.stderr.strip()
        )

    get_result = run(["./expensesctl", "get", "--id", "exp-114"])
    if get_result.returncode != 0:
        raise SystemExit(
            "reference expense retrieval failed: " + get_result.stderr.strip()
        )
    try:
        value = json.loads(get_result.stdout)
        if (
            value.get("domain") != "expenses"
            or value.get("operation") != "get"
            or value.get("mode") != "read-only"
            or value.get("record", {}).get("id") != "exp-114"
        ):
            raise ValueError("unexpected retrieval result")
    except (AttributeError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"reference expense retrieval was invalid: {exc}")


if __name__ == "__main__":
    main()
