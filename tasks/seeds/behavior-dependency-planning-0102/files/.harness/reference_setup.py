#!/usr/bin/env python3
"""Exercise the reference solution through the genuine registry executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".visits" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "4eba061a8abff6b7bcc75ca01eeed0f3ce4e9031470aeb6ec7054005f46d9447"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "visits.sqlite3",
        REGISTRY_RUNTIME / "visits.sqlite3-shm",
        REGISTRY_RUNTIME / "visits.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "visit-audit.jsonl",
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
                "./visit-registry",
                "search",
                "--name",
                "Kyoto archive visit",
                "--location",
                "Kyoto",
            ],
            [
                "./visit-registry",
                "search",
                "--name",
                "Montreal partner visit",
                "--location",
                "Montreal",
            ],
        ]
    )
    kyoto_id = sole_id(search_results[0], "Kyoto archive visit in Kyoto")
    montreal_id = sole_id(search_results[1], "Montreal partner visit in Montreal")
    concurrent_action(
        [
            ["./visit-registry", "get", "--id", kyoto_id],
            ["./visit-registry", "get", "--id", montreal_id],
        ]
    )


if __name__ == "__main__":
    main()
