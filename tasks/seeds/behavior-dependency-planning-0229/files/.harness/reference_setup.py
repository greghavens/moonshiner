#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine account executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".accounts" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "99086062165c46c4bdf805dea982019a0604ee27098e120c70c510e80e6545a0"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "accounts.sqlite3",
        REGISTRY_RUNTIME / "accounts.sqlite3-shm",
        REGISTRY_RUNTIME / "accounts.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "account-audit.jsonl",
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
                "./account-registry",
                "search",
                "--name",
                "Cobalt Museum sponsorship",
                "--region",
                "Northeast",
            ],
            [
                "./account-registry",
                "search",
                "--name",
                "Delta Housing expansion",
                "--region",
                "Southwest",
            ],
        ]
    )
    cobalt_id = sole_id(
        search_results[0], "Cobalt Museum sponsorship in Northeast"
    )
    delta_id = sole_id(
        search_results[1], "Delta Housing expansion in Southwest"
    )
    concurrent_action(
        [
            ["./account-registry", "get", "--id", cobalt_id],
            ["./account-registry", "get", "--id", delta_id],
        ]
    )


if __name__ == "__main__":
    main()
