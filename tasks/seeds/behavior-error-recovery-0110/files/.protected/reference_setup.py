#!/usr/bin/env python3
"""Exercise the reference recovery path through the genuine executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "program-audit.jsonl"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "195b6ca275fb06a64959ae411823f718a9d1e42930ee65befa44fbb183a16a15"

VOLUNTEER_COMMAND = [
    "./program-board",
    "availability",
    "--task",
    "Volunteer handbook refresh",
    "--program",
    "Community Program",
    "--date",
    "2026-10-29",
]
FLEET_COMMAND = [
    "./program-board",
    "availability",
    "--task",
    "Fleet electrification study",
    "--program",
    "Transit Program",
    "--date",
    "2026-10-29",
]


def run_process(command: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def decode_json(raw: bytes, label: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"reference {label} did not return JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"reference {label} returned a non-object")
    return payload


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    AUDIT_PATH.unlink(missing_ok=True)

    volunteer_process = run_process(VOLUNTEER_COMMAND)
    fleet_process = run_process(FLEET_COMMAND)
    volunteer_stdout, volunteer_stderr = volunteer_process.communicate(timeout=5)
    fleet_stdout, fleet_stderr = fleet_process.communicate(timeout=5)

    if volunteer_process.returncode != 0 or volunteer_stderr:
        raise RuntimeError("reference volunteer branch did not succeed cleanly")
    volunteer = decode_json(volunteer_stdout, "volunteer branch")
    if volunteer.get("availability") != "available":
        raise RuntimeError("reference volunteer availability was unexpected")

    if fleet_process.returncode != 75 or fleet_stdout:
        raise RuntimeError("reference fleet branch did not return a transient error")
    fleet_error = decode_json(fleet_stderr, "fleet transient error")
    if (
        fleet_error.get("kind") != "transient"
        or fleet_error.get("retryable") is not True
    ):
        raise RuntimeError("reference fleet failure was not explicitly retryable")

    retry_process = run_process(FLEET_COMMAND)
    retry_stdout, retry_stderr = retry_process.communicate(timeout=5)
    if retry_process.returncode != 0 or retry_stderr:
        raise RuntimeError("reference fleet retry did not succeed cleanly")
    retry = decode_json(retry_stdout, "fleet retry")
    if retry.get("availability") != "unavailable":
        raise RuntimeError("reference fleet availability was unexpected")


if __name__ == "__main__":
    main()
