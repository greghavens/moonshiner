#!/usr/bin/env python3
"""Exercise the reference solution through the genuine message executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".messages" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
REPORT_PATH = ROOT / "status_audit.md"
REPORT_DIGEST = "ccd8c3920f27e75aded0607f19303cc02e0c3bb60cade19e3a4b5c1f824bc6c7"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "messages.sqlite3",
        REGISTRY_RUNTIME / "messages.sqlite3-shm",
        REGISTRY_RUNTIME / "messages.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "message-audit.jsonl",
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
                f"reference registry operation failed: {stderr.decode().strip()}"
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
                "./message-registry",
                "search",
                "--name",
                "Volunteer renewal reminder",
                "--location",
                "Volunteers",
            ],
            [
                "./message-registry",
                "search",
                "--name",
                "North team quarterly update",
                "--location",
                "North Team",
            ],
        ]
    )
    volunteer_id = sole_id(
        search_results[0], "Volunteer renewal reminder in Volunteers"
    )
    north_id = sole_id(
        search_results[1], "North team quarterly update in North Team"
    )
    concurrent_action(
        [
            ["./message-registry", "get", "--id", volunteer_id],
            ["./message-registry", "get", "--id", north_id],
        ]
    )


if __name__ == "__main__":
    main()
