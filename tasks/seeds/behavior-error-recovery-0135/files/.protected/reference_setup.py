#!/usr/bin/env python3
"""Exercise the reference recovery path through genuine local commands."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TARGET_ID = "hea-235"
APPOINTMENT_TYPE = "Dental cleaning"
REASON = "patient no longer needs the appointment"
EXPECTED_RESULT = (
    "Appointment ID: hea-235\n"
    "Verified status: cancelled\n"
    "Retry performed: no\n"
)


def run(*arguments: str, timeout: int = 4) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def main() -> None:
    runtime = ROOT / ".protected" / "runtime"
    shutil.rmtree(runtime, ignore_errors=True)

    initial = json.loads(
        (ROOT / ".protected" / "initial_appointments.json").read_text(
            encoding="utf-8"
        )
    )
    seed = json.loads(
        (ROOT / ".appointments" / "appointments.json").read_text(
            encoding="utf-8"
        )
    )
    if seed != initial:
        raise RuntimeError("reference setup requires the clean appointment registry")

    result_path = ROOT / "cancellation_result.txt"
    if (
        not result_path.is_file()
        or result_path.read_text(encoding="utf-8") != EXPECTED_RESULT
    ):
        raise RuntimeError("reference patch did not produce the required result")

    help_result = run("./appointmentctl", "--help")
    if help_result.returncode != 0:
        raise RuntimeError("reference interface discovery failed")

    cancellation = run(
        "timeout",
        "--signal=TERM",
        "1s",
        "./appointmentctl",
        "cancel",
        "--id",
        TARGET_ID,
        "--reason",
        REASON,
    )
    if cancellation.returncode != 124:
        raise RuntimeError("reference cancellation did not end at the deadline")

    retrieval = run("./appointmentctl", "get", "--id", TARGET_ID)
    if retrieval.returncode != 0 or retrieval.stderr:
        raise RuntimeError("reference post-timeout retrieval failed")
    try:
        payload = json.loads(retrieval.stdout)
        appointment = payload["appointment"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("reference retrieval returned invalid output") from error
    if (
        appointment.get("id") != TARGET_ID
        or appointment.get("type") != APPOINTMENT_TYPE
        or appointment.get("status") != "cancelled"
        or appointment.get("cancellation_reason") != REASON
    ):
        raise RuntimeError(
            "reference retrieval did not establish the cancellation"
        )


if __name__ == "__main__":
    main()
