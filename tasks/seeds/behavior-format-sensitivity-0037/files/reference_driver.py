#!/usr/bin/env python3
"""Reference proof that executes the genuine work-order opener."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent
LEDGER_PATH = ROOT / ".work-order-evidence" / "session.jsonl"
EXECUTABLE = ROOT / "work_order_open"


def main() -> int:
    # Trace generation also invokes reference_setup against the untouched
    # baseline. There is no reference evidence to prove in that lifecycle.
    if not LEDGER_PATH.is_file():
        return 0

    expected_evidence = LEDGER_PATH.read_bytes()
    LEDGER_PATH.unlink()
    completed = subprocess.run(
        [str(EXECUTABLE), "fac-137"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip() or "work-order open failed"
        )
    if completed.stderr:
        raise RuntimeError("work-order open wrote unexpected stderr")
    payload = json.loads(completed.stdout)
    if (
        not isinstance(payload, dict)
        or payload.get("id") != "fac-137"
        or set(payload)
        != {
            "id",
            "site",
            "summary",
            "priority",
            "assigned_team",
            "opened_at",
            "status",
        }
    ):
        raise RuntimeError("work-order opener returned the wrong record")
    if LEDGER_PATH.read_bytes() != expected_evidence:
        raise RuntimeError(
            "work-order executable did not recreate reference evidence"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
