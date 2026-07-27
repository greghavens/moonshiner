#!/usr/bin/env python3
"""Initialize a disposable library trace from protected source data."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".library"
SEED = ROOT / ".protected" / "library_seed.json"
STATE = RUNTIME / "state.json"
LOCK = RUNTIME / "state.lock"
REPORT = ROOT / "cancellation-report.txt"
CLIENT = ROOT / "libraryctl"
TARGET_ID = "lib-133"
REASON = "record superseded by a corrected request"


def run_client(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLIENT), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5,
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def complete_reference_workflow() -> None:
    discovery = run_client(["--help"])
    require(discovery.returncode == 0, "reference interface discovery failed")

    cancellation_arguments = [
        "cancel",
        "--id",
        TARGET_ID,
        "--reason",
        REASON,
    ]
    cancellation = run_client(cancellation_arguments)
    require(
        cancellation.returncode == 124
        and "outcome is unknown" in cancellation.stderr,
        "reference cancellation did not exercise the client deadline",
    )

    retrieval = run_client(["fetch", "--id", TARGET_ID])
    require(retrieval.returncode == 0, "reference direct fetch failed")
    try:
        payload: Any = json.loads(retrieval.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("reference direct fetch returned invalid JSON") from error
    record = payload.get("record") if isinstance(payload, dict) else None
    require(
        isinstance(record, dict) and record.get("id") == TARGET_ID,
        "reference direct fetch returned the wrong library record",
    )
    observed_status = record.get("status")
    require(observed_status == "active", "reference direct fetch was not active")

    retry = run_client(cancellation_arguments)
    require(retry.returncode == 0, "reference conditional retry failed")
    try:
        retry_payload: Any = json.loads(retry.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("reference conditional retry returned invalid JSON") from error
    retried_record = (
        retry_payload.get("record") if isinstance(retry_payload, dict) else None
    )
    require(
        isinstance(retried_record, dict)
        and retried_record.get("status") == "cancelled"
        and retried_record.get("cancellation_reason") == REASON,
        "reference conditional retry did not cancel with the exact reason",
    )

    REPORT.write_text(
        f"Verification observed {TARGET_ID} status: {observed_status}.\n"
        "Conditional retry necessary: yes.\n",
        encoding="utf-8",
    )


def main() -> int:
    reference_mode = (
        REPORT.is_file()
        and not REPORT.is_symlink()
        and bool(REPORT.stat().st_mode & 0o111)
    )
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)
    shutil.copyfile(SEED, STATE)
    LOCK.touch(mode=0o600)
    if REPORT.exists() and not reference_mode:
        REPORT.unlink()

    if reference_mode:
        complete_reference_workflow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
