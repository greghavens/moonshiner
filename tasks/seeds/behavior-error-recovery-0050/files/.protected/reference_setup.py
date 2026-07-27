#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine local executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".availability-runtime"
MARKER = ROOT / ".reference_solution"
REPORT = ROOT / "availability-report.txt"
MARKER_DIGEST = "ff8caab8853d0cb229260ba8e0d2ff73b9e1e2aafaae3832f5efed33ee54db2f"

POLICY = [
    "./availability-check",
    "check",
    "--item",
    "Policy review",
    "--site",
    "Beacon",
    "--date",
    "2026-09-23",
]
NEWSLETTER = [
    "./availability-check",
    "check",
    "--item",
    "Newsletter draft",
    "--site",
    "Clover",
    "--date",
    "2026-09-23",
]


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "execution.jsonl").unlink(missing_ok=True)

    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        for command in (POLICY, NEWSLETTER)
    ]
    results = [process.communicate() for process in processes]
    codes = [process.returncode for process in processes]
    if codes != [0, 75]:
        raise RuntimeError(f"unexpected initial reference exit codes: {codes}")

    policy = json.loads(results[0][0])
    if set(policy) != {"item", "site", "date", "availability"}:
        raise RuntimeError("Policy review reference check returned an invalid response")
    transient = json.loads(results[1][1])
    if transient.get("retryable") is not True:
        raise RuntimeError("Newsletter draft reference check was not retryable")

    retry = subprocess.run(
        NEWSLETTER,
        cwd=ROOT,
        capture_output=True,
        text=True,
        start_new_session=True,
        check=True,
    )
    newsletter = json.loads(retry.stdout)
    if set(newsletter) != {"item", "site", "date", "availability"}:
        raise RuntimeError("Newsletter draft reference retry returned an invalid response")

    report = (
        f"- {policy['item']} at {policy['site']}: {policy['availability']}\n"
        f"- {newsletter['item']} at {newsletter['site']}: "
        f"{newsletter['availability']}\n"
    )
    REPORT.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
