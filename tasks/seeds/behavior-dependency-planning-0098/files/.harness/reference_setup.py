#!/usr/bin/env python3
"""Exercise the reference solution through the genuine account executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".telecom" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "653b34d9c8648341e729f29394bdb13a961e7874c9f57060ac620f7d6fb63505"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "telecom.sqlite3",
        REGISTRY_RUNTIME / "telecom.sqlite3-shm",
        REGISTRY_RUNTIME / "telecom.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "telecom-audit.jsonl",
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
                "./telecom-account",
                "search",
                "--name",
                "Family fiber subscription",
                "--account",
                "Family Account",
            ],
            [
                "./telecom-account",
                "search",
                "--name",
                "Studio tablet plan",
                "--account",
                "Studio Account",
            ],
        ]
    )
    family_id = sole_id(
        search_results[0], "Family fiber subscription in Family Account"
    )
    studio_id = sole_id(
        search_results[1], "Studio tablet plan in Studio Account"
    )
    concurrent_action(
        [
            ["./telecom-account", "get", "--id", family_id],
            ["./telecom-account", "get", "--id", studio_id],
        ]
    )


if __name__ == "__main__":
    main()
