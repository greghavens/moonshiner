#!/usr/bin/env python3
"""Exercise the reference solution through the genuine registry executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".calendar" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "689a96e60b7bd32509a9f73c123f0670147ccce80005e05fe05d19e3a58a677c"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "calendar.sqlite3",
        REGISTRY_RUNTIME / "calendar.sqlite3-shm",
        REGISTRY_RUNTIME / "calendar.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "calendar-audit.jsonl",
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
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    search_results = concurrent_action(
        [
            [
                "./calendar-registry",
                "search",
                "--name",
                "Vendor onboarding review",
                "--location",
                "Denver HQ",
            ],
            [
                "./calendar-registry",
                "search",
                "--name",
                "Quarterly finance check-in",
                "--location",
                "Chicago Office",
            ],
        ]
    )
    vendor_id = sole_id(
        search_results[0], "Vendor onboarding review in Denver HQ"
    )
    finance_id = sole_id(
        search_results[1], "Quarterly finance check-in in Chicago Office"
    )
    concurrent_action(
        [
            ["./calendar-registry", "get", "--id", vendor_id],
            ["./calendar-registry", "get", "--id", finance_id],
        ]
    )


if __name__ == "__main__":
    main()
