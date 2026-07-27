#!/usr/bin/env python3
"""Exercise the reference recovery through the genuine clinic executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LEDGER_RUNTIME = ROOT / ".health_admin" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
RECEIPT_PATH = ROOT / "cancellation_receipt.md"
RECEIPT_DIGEST = "32e34e78b9a6c9b5e6def84e709823ac86ff63a5b9ce6f8365017eae13cc83f6"
REASON = "duplicate record entered during intake"


def reset_generated_state() -> None:
    for path in (
        LEDGER_RUNTIME / "clinic.sqlite3",
        LEDGER_RUNTIME / "clinic.sqlite3-shm",
        LEDGER_RUNTIME / "clinic.sqlite3-wal",
        LEDGER_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "clinic-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def invoke(arguments: list[str], expected_code: int) -> dict:
    process = subprocess.run(
        ["./clinic-admin", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    if process.returncode != expected_code:
        raise RuntimeError(
            f"reference clinic operation exited {process.returncode}: "
            f"{process.stderr.strip()}"
        )
    stream = process.stdout if process.returncode == 0 else process.stderr
    return json.loads(stream)


def main() -> None:
    if (
        not RECEIPT_PATH.is_file()
        or hashlib.sha256(RECEIPT_PATH.read_bytes()).hexdigest() != RECEIPT_DIGEST
    ):
        return

    reset_generated_state()
    timeout = invoke(
        ["cancel", "--id", "hea-135", "--reason", REASON], 124
    )
    if timeout.get("outcome") != "unknown":
        raise RuntimeError("reference cancellation did not produce unknown outcome")
    verification = invoke(["get", "--id", "hea-135"], 0)
    record = verification.get("record") or {}
    if record.get("status") == "active":
        result = invoke(
            ["cancel", "--id", "hea-135", "--reason", REASON], 0
        )
        if (result.get("record") or {}).get("status") != "cancelled":
            raise RuntimeError("reference retry did not cancel the appointment")


if __name__ == "__main__":
    main()
