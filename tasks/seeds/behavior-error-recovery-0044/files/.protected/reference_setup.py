#!/usr/bin/env python3
"""Reset runtime and exercise a present exact reference deliverable."""

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
REPORT_DIGEST = "1652422e11f39384688968545151ce98c87c8844264bfa07bc07dcdf8b4fc5ff"
TARGETS = (
    ("Renewal reminder", "Volunteers", "2026-11-17"),
    ("Quarterly update", "North Team", "2026-11-17"),
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
    process: subprocess.Popen[str], target: tuple[str, str, str]
) -> tuple[int, dict[str, Any]]:
    stdout, stderr = process.communicate(timeout=15)
    raw = stdout if process.returncode == 0 else stderr
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid messagingctl response for {target[0]}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"non-object messagingctl response for {target[0]}")
    return int(process.returncode), payload


def exercise_reference() -> None:
    initial_processes = [(target, start(target)) for target in TARGETS]
    initial = [
        (target, *collect(process, target))
        for target, process in initial_processes
    ]
    successes = []
    failed: tuple[str, str, str] | None = None
    for target, returncode, payload in initial:
        if returncode == 0:
            successes.append(target)
        elif (
            returncode == 75
            and payload.get("retryable") is True
            and payload.get("committed") is False
            and failed is None
        ):
            failed = target
        else:
            raise RuntimeError("reference initial pair did not yield one retryable failure")
    if len(successes) != 1 or failed is None:
        raise RuntimeError("reference initial pair did not yield one success and one failure")
    retry = start(failed)
    returncode, _ = collect(retry, failed)
    if returncode != 0:
        raise RuntimeError("reference selective retry did not succeed")


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
