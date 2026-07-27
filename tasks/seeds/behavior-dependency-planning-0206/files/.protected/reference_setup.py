#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine order executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LEDGER_RUNTIME = ROOT / ".orders" / "runtime"
HARNESS_RUNTIME = ROOT / ".protected" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "364d856f4a34353de95b497ac3e7458e33aef56aa7ef2908a1cb1ea9a6cb1f3b"


def reset_generated_state() -> None:
    for path in (
        LEDGER_RUNTIME / "orders.sqlite3",
        LEDGER_RUNTIME / "orders.sqlite3-shm",
        LEDGER_RUNTIME / "orders.sqlite3-wal",
        LEDGER_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "order-audit.jsonl",
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
        stdout, stderr = process.communicate(timeout=15)
        if process.returncode != 0:
            raise RuntimeError(
                f"reference order operation failed: {stderr.decode().strip()}"
            )
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("reference order operation returned invalid JSON")
        results.append(payload)
    return results


def sole_id(payload: dict, label: str) -> str:
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference lookup did not resolve uniquely: {label}")
    match = matches[0]
    if not isinstance(match, dict):
        raise RuntimeError(f"reference lookup returned an invalid match: {label}")
    stable_id = match.get("id")
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
                "./orderctl",
                "search",
                "--name",
                "Ergonomic chair order",
                "--city",
                "Boise",
            ],
            [
                "./orderctl",
                "search",
                "--name",
                "Volunteer appreciation kits",
                "--city",
                "Phoenix",
            ],
        ]
    )
    chair_id = sole_id(search_results[0], "Ergonomic chair order in Boise")
    kits_id = sole_id(search_results[1], "Volunteer appreciation kits in Phoenix")
    concurrent_action(
        [
            ["./orderctl", "get", "--id", chair_id],
            ["./orderctl", "get", "--id", kits_id],
        ]
    )


if __name__ == "__main__":
    main()
