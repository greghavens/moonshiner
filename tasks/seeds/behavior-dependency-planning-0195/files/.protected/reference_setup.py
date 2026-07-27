#!/usr/bin/env python3
"""Exercise the reference path through the genuine schedule executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCHEDULE_RUNTIME = ROOT / ".clinic" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
REPORT_PATH = ROOT / "appointment-audit.md"
REPORT_DIGEST = "0ed63cb2ba42a46942c70af0f234f595d887143345798ebdc93c8a22bdc08e02"


def reset_generated_state() -> None:
    for path in (
        SCHEDULE_RUNTIME / "schedule.sqlite3",
        SCHEDULE_RUNTIME / "schedule.sqlite3-shm",
        SCHEDULE_RUNTIME / "schedule.sqlite3-wal",
        SCHEDULE_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "schedule-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def concurrent_action(commands: list[list[str]]) -> list[dict]:
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        for command in commands
    ]
    results: list[dict] = []
    for process in processes:
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"reference schedule operation failed: {stderr.decode().strip()}"
            )
        results.append(json.loads(stdout))
    return results


def sole_id(payload: dict, label: str) -> str:
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference lookup did not resolve uniquely: {label}")
    stable_id = matches[0].get("stable_id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError(f"reference lookup returned no stable ID: {label}")
    return stable_id


def main() -> None:
    if (
        not REPORT_PATH.is_file()
        or hashlib.sha256(REPORT_PATH.read_bytes()).hexdigest() != REPORT_DIGEST
    ):
        return

    reset_generated_state()
    search_results = concurrent_action(
        [
            [
                "./clinic-schedule",
                "search",
                "--appointment",
                "Dental cleaning — Morgan Lee",
                "--clinic",
                "Cedar Clinic",
            ],
            [
                "./clinic-schedule",
                "search",
                "--appointment",
                "Lab follow-up — Avery Chen",
                "--clinic",
                "Dale Clinic",
            ],
        ]
    )
    dental_id = sole_id(
        search_results[0], "Dental cleaning — Morgan Lee in Cedar Clinic"
    )
    lab_id = sole_id(
        search_results[1], "Lab follow-up — Avery Chen in Dale Clinic"
    )
    concurrent_action(
        [
            ["./clinic-schedule", "get", "--id", dental_id],
            ["./clinic-schedule", "get", "--id", lab_id],
        ]
    )


if __name__ == "__main__":
    main()
