#!/usr/bin/env python3
"""Exercise the reference solution through the genuine hospitality executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".hospitality" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "278aa0bef22e131c65dec4307d11a4d7f3ab25aa7f68668a4a18b504e146174c"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "hospitality.sqlite3",
        REGISTRY_RUNTIME / "hospitality.sqlite3-shm",
        REGISTRY_RUNTIME / "hospitality.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "hospitality-audit.jsonl",
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
                f"reference hospitality operation failed: {stderr.decode().strip()}"
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
                "./hospitality-registry",
                "search",
                "--name",
                "Maple Hall reception",
                "--location",
                "Austin",
            ],
            [
                "./hospitality-registry",
                "search",
                "--name",
                "Orchid Suite lodging",
                "--location",
                "Raleigh",
            ],
        ]
    )
    maple_id = sole_id(search_results[0], "Maple Hall reception in Austin")
    orchid_id = sole_id(search_results[1], "Orchid Suite lodging in Raleigh")
    concurrent_action(
        [
            ["./hospitality-registry", "get", "--id", maple_id],
            ["./hospitality-registry", "get", "--id", orchid_id],
        ]
    )


if __name__ == "__main__":
    main()
