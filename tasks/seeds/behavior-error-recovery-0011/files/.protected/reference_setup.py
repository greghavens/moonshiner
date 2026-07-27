#!/usr/bin/env python3
"""Exercise the reference recovery flow through the genuine executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "cancellation_audit.txt"
RUNTIME = ROOT / ".candidate-runtime"
REPORT_DIGEST = "0e5fbc264c760202bba6bef4bcd6c09273a6665f08729d501b61715701f15529"
STABLE_ID = "rec-111"
REASON = "request withdrawn by its owner"


def invoke(arguments: list[str], expected_code: int) -> dict | None:
    result = subprocess.run(
        ["./candidate-records", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode != expected_code:
        raise RuntimeError(
            f"reference operation returned {result.returncode}, "
            f"expected {expected_code}"
        )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def main() -> None:
    if (
        not REPORT.is_file()
        or hashlib.sha256(REPORT.read_bytes()).hexdigest() != REPORT_DIGEST
    ):
        return

    shutil.rmtree(RUNTIME, ignore_errors=True)
    invoke(
        ["cancel", "--id", STABLE_ID, "--reason", REASON],
        expected_code=75,
    )
    verification = invoke(["get", "--id", STABLE_ID], expected_code=0)
    if verification is None:
        raise RuntimeError("reference retrieval returned no record")
    status = verification.get("record", {}).get("status")

    if status == "active":
        retry = invoke(
            ["cancel", "--id", STABLE_ID, "--reason", REASON],
            expected_code=0,
        )
        if retry is None or retry.get("record", {}).get("status") != "cancelled":
            raise RuntimeError("reference retry did not cancel the candidate")
    elif status != "cancelled":
        raise RuntimeError(f"reference retrieval returned unexpected status: {status}")


if __name__ == "__main__":
    main()
