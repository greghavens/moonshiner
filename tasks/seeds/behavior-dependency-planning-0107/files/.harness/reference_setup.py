#!/usr/bin/env python3
"""Exercise the reference solution through the genuine inventory executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".inventory" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "6087f9df075d4200b778434b7f00730ed6235eb5135e82c9304257f9e220acde"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "inventory.sqlite3",
        REGISTRY_RUNTIME / "inventory.sqlite3-shm",
        REGISTRY_RUNTIME / "inventory.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "inventory-audit.jsonl",
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
                "./inventory-registry",
                "search",
                "--name",
                "Recycled packing tape",
                "--warehouse",
                "Warehouse C",
            ],
            [
                "./inventory-registry",
                "search",
                "--name",
                "A4 printer paper",
                "--warehouse",
                "Warehouse D",
            ],
        ]
    )
    tape_id = sole_id(
        search_results[0], "Recycled packing tape in Warehouse C"
    )
    paper_id = sole_id(search_results[1], "A4 printer paper in Warehouse D")
    concurrent_action(
        [
            ["./inventory-registry", "get", "--id", tape_id],
            ["./inventory-registry", "get", "--id", paper_id],
        ]
    )


if __name__ == "__main__":
    main()
