#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine registry executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "cancellation_audit.txt"
RUNTIME = ROOT / ".library-runtime"
REPORT_DIGEST = "5100165379d285f3a3da82af644d024f7b1556555d89f8e9714df924fba62fe2"


def main() -> None:
    if (
        not REPORT.is_file()
        or hashlib.sha256(REPORT.read_bytes()).hexdigest() != REPORT_DIGEST
    ):
        return

    shutil.rmtree(RUNTIME, ignore_errors=True)
    first = subprocess.run(
        [
            "./library-records",
            "cancel",
            "--id",
            "lib-113",
            "--reason",
            "record superseded by a corrected request",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if first.returncode != 75:
        raise RuntimeError(f"reference cancellation returned {first.returncode}")

    verification = subprocess.run(
        ["./library-records", "get", "--id", "lib-113"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(verification.stdout)
    if payload.get("record", {}).get("status") != "cancelled":
        raise RuntimeError("reference verification did not settle the outcome")


if __name__ == "__main__":
    main()
