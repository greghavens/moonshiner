#!/usr/bin/env python3
"""Exercise the reference recovery path through genuine local commands."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "c0dc45e38de8f3f4e8c343c6a95d97c38abd5fcc031429db47a8e66dc86a6a0c"
TARGET_ID = "hea-215"
REASON = "patient requested cancellation"


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
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    runtime = ROOT / ".protected" / "runtime"
    shutil.rmtree(runtime, ignore_errors=True)

    initial = json.loads(
        (ROOT / ".protected" / "initial_appointments.json").read_text(
            encoding="utf-8"
        )
    )
    seed = json.loads(
        (ROOT / ".clinic" / "appointments.json").read_text(encoding="utf-8")
    )
    if seed != initial:
        raise RuntimeError("reference setup requires the clean appointment registry")

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
        or appointment.get("name") != "Dental cleaning 115"
        or appointment.get("status") != "cancelled"
        or appointment.get("cancellation_reason") != REASON
    ):
        raise RuntimeError("reference retrieval did not establish cancellation")


if __name__ == "__main__":
    main()
