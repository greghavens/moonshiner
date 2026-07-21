#!/usr/bin/env python3
"""Exercise the reference solution through the genuine registry executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".claims" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "5c7e7019dfc1da31e5c35c4e6d57486a3b4c204a5f5b834f3360d5c978c8f38b"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "claims.sqlite3",
        REGISTRY_RUNTIME / "claims.sqlite3-shm",
        REGISTRY_RUNTIME / "claims.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "claim-audit.jsonl",
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
                "./claim-registry",
                "search",
                "--name",
                "Bicycle theft claim",
                "--office",
                "West Office",
            ],
            [
                "./claim-registry",
                "search",
                "--name",
                "Windshield damage claim",
                "--office",
                "North Office",
            ],
        ]
    )
    bicycle_id = sole_id(search_results[0], "Bicycle theft claim in West Office")
    windshield_id = sole_id(
        search_results[1], "Windshield damage claim in North Office"
    )
    concurrent_action(
        [
            ["./claim-registry", "get", "--id", bicycle_id],
            ["./claim-registry", "get", "--id", windshield_id],
        ]
    )


if __name__ == "__main__":
    main()
