#!/usr/bin/env python3
"""Exercise the reference solution through the genuine ledger executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SHIPMENT_RUNTIME = ROOT / ".shipments" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
REPORT_PATH = ROOT / "shipment_handoff.md"
REPORT_DIGEST = "8095f103ebbce3efba4a67c4db722cea90a18dbaafa878449f580a8e122068fc"


def reset_generated_state() -> None:
    for path in (
        SHIPMENT_RUNTIME / "shipments.sqlite3",
        SHIPMENT_RUNTIME / "shipments.sqlite3-shm",
        SHIPMENT_RUNTIME / "shipments.sqlite3-wal",
        SHIPMENT_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "shipment-audit.jsonl",
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
                f"reference ledger operation failed: {stderr.decode().strip()}"
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
                "./shipment-ledger",
                "search",
                "--shipment",
                "Library transfer cartons",
                "--city",
                "Portland",
            ],
            [
                "./shipment-ledger",
                "search",
                "--shipment",
                "Vaccine cooler shipment",
                "--city",
                "Denver",
            ],
        ]
    )
    library_id = sole_id(
        search_results[0], "Library transfer cartons in Portland"
    )
    vaccine_id = sole_id(
        search_results[1], "Vaccine cooler shipment in Denver"
    )
    concurrent_action(
        [
            ["./shipment-ledger", "get", "--id", library_id],
            ["./shipment-ledger", "get", "--id", vaccine_id],
        ]
    )


if __name__ == "__main__":
    main()
