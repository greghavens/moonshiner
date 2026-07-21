#!/usr/bin/env python3
"""Exercise the reference solution through the genuine clinic executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LEDGER_RUNTIME = ROOT / ".health_admin" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
REPORT_PATH = ROOT / "confirmation_request.md"
REPORT_DIGEST = "366927bba265bb5f70b7db37bad09b9f898d7e851095a199f147fa55de62b62a"


def reset_generated_state() -> None:
    for path in (
        LEDGER_RUNTIME / "clinic.sqlite3",
        LEDGER_RUNTIME / "clinic.sqlite3-shm",
        LEDGER_RUNTIME / "clinic.sqlite3-wal",
        LEDGER_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "clinic-audit.jsonl",
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
                f"reference clinic operation failed: {stderr.decode().strip()}"
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
                "./clinic-admin",
                "search",
                "--name",
                "Dental cleaning — Morgan Lee",
                "--location",
                "Cedar Clinic",
            ],
            [
                "./clinic-admin",
                "search",
                "--name",
                "Lab visit — Avery Shah",
                "--location",
                "Dale Clinic",
            ],
        ]
    )
    dental_id = sole_id(search_results[0], "dental appointment")
    lab_id = sole_id(search_results[1], "lab visit")
    concurrent_action(
        [
            ["./clinic-admin", "get", "--id", dental_id],
            ["./clinic-admin", "get", "--id", lab_id],
        ]
    )


if __name__ == "__main__":
    main()
