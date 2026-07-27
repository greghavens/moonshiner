#!/usr/bin/env python3
"""Exercise the reference solution through genuine messagingctl processes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".messaging-runtime"
CLIENT = ROOT / "messagingctl"
REPORT = ROOT / "availability-report.md"
REPORT_DIGEST = "8fa6a19926bb8f5d1b5590d6d4cd9113c22bc9bd9b4b253b09b3e50a3f5fb2fa"
TARGETS = (
    ("Renewal reminder 124", "Volunteers", "2026-11-25"),
    ("Quarterly update 124", "North Team", "2026-11-25"),
)


def command_for(target: tuple[str, str, str]) -> list[str]:
    name, location, availability_date = target
    return [
        str(CLIENT),
        "availability",
        "--name",
        name,
        "--location",
        location,
        "--date",
        availability_date,
    ]


def start(target: tuple[str, str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command_for(target),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def collect(
    process: subprocess.Popen[str],
    target: tuple[str, str, str],
) -> tuple[int, dict[str, Any]]:
    stdout, stderr = process.communicate(timeout=15)
    raw = stdout if process.returncode == 0 else stderr
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid executable response for {target[0]}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"non-object executable response for {target[0]}")
    return int(process.returncode), payload


def exercise_reference() -> None:
    processes = [(target, start(target)) for target in TARGETS]
    initial = [
        (target, *collect(process, target))
        for target, process in processes
    ]
    success_count = 0
    failed: tuple[str, str, str] | None = None
    for target, returncode, payload in initial:
        if returncode == 0 and isinstance(payload.get("available"), bool):
            success_count += 1
        elif (
            returncode == 75
            and payload.get("transient") is True
            and payload.get("retryable") is True
            and payload.get("committed") is False
            and failed is None
        ):
            failed = target
        else:
            raise RuntimeError("reference initial pair had an unexpected outcome")
    if success_count != 1 or failed is None:
        raise RuntimeError("reference initial pair did not have one partial failure")

    retry = start(failed)
    returncode, payload = collect(retry, failed)
    if returncode != 0 or not isinstance(payload.get("available"), bool):
        raise RuntimeError("reference selective retry did not return a result")


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    if (
        REPORT.is_file()
        and hashlib.sha256(REPORT.read_bytes()).hexdigest() == REPORT_DIGEST
    ):
        exercise_reference()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
