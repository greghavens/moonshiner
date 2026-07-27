#!/usr/bin/env python3
"""Reference proof that executes the genuine recruiting opener."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent
LEDGER_PATH = ROOT / ".recruiting-evidence" / "session.jsonl"
EXECUTABLE = ROOT / "recruiting_open"


def main() -> int:
    # Normal trace setup runs before the agent has acted. Reference validation
    # runs this same hook after applying the reference patch.
    if not LEDGER_PATH.exists():
        return 0

    expected_evidence = LEDGER_PATH.read_bytes()
    LEDGER_PATH.unlink()
    completed = subprocess.run(
        [str(EXECUTABLE), "rec-131"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "candidate open failed")
    if completed.stderr:
        raise RuntimeError("candidate open wrote unexpected stderr")
    payload = json.loads(completed.stdout)
    if (
        not isinstance(payload, dict)
        or payload.get("id") != "rec-131"
        or set(payload)
        != {
            "id",
            "full_name",
            "role",
            "stage",
            "recruiter",
        }
    ):
        raise RuntimeError("recruiting opener returned the wrong record")
    if LEDGER_PATH.read_bytes() != expected_evidence:
        raise RuntimeError("recruiting executable did not recreate reference evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
