#!/usr/bin/env python3
"""Exercise the reference solution through the genuine order executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".orders" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "b5495d4b30b3deb714c93259c0a117fd03ff8b9cf2343d7f05090806f9725a71"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "orders.sqlite3",
        REGISTRY_RUNTIME / "orders.sqlite3-shm",
        REGISTRY_RUNTIME / "orders.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
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
                "./order-registry",
                "search",
                "--name",
                "Ergonomic chair order",
                "--location",
                "Boise",
            ],
            [
                "./order-registry",
                "search",
                "--name",
                "Welcome-kit order",
                "--location",
                "Phoenix",
            ],
        ]
    )
    chair_id = sole_id(search_results[0], "Ergonomic chair order in Boise")
    welcome_kit_id = sole_id(search_results[1], "Welcome-kit order in Phoenix")
    concurrent_action(
        [
            ["./order-registry", "get", "--id", chair_id],
            ["./order-registry", "get", "--id", welcome_kit_id],
        ]
    )


if __name__ == "__main__":
    main()
