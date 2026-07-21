#!/usr/bin/env python3
"""Exercise the reference solution through the genuine registry executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".facilities" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "8b717839c4c1b5837dd50538b8cb290c0467467ce705ab25c9d63afca2362807"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "facilities.sqlite3",
        REGISTRY_RUNTIME / "facilities.sqlite3-shm",
        REGISTRY_RUNTIME / "facilities.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "facilities-audit.jsonl",
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
                "./facilities-registry",
                "search",
                "--name",
                "Atrium lighting repair",
                "--location",
                "Building A",
            ],
            [
                "./facilities-registry",
                "search",
                "--name",
                "Training room setup",
                "--location",
                "Building B",
            ],
        ]
    )
    atrium_id = sole_id(
        search_results[0], "Atrium lighting repair in Building A"
    )
    training_id = sole_id(
        search_results[1], "Training room setup in Building B"
    )
    concurrent_action(
        [
            ["./facilities-registry", "get", "--id", atrium_id],
            ["./facilities-registry", "get", "--id", training_id],
        ]
    )


if __name__ == "__main__":
    main()
