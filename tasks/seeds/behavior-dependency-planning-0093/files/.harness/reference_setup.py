#!/usr/bin/env python3
"""Exercise the reference solution through the genuine catalog executable."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CATALOG_RUNTIME = ROOT / ".library" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"


def reset_generated_state() -> None:
    for path in (
        CATALOG_RUNTIME / "catalog.sqlite3",
        CATALOG_RUNTIME / "catalog.sqlite3-shm",
        CATALOG_RUNTIME / "catalog.sqlite3-wal",
        CATALOG_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "catalog-audit.jsonl",
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
                f"reference catalog operation failed: {stderr.decode().strip()}"
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
    reset_generated_state()
    search_results = concurrent_action(
        [
            [
                "./library-catalog",
                "search",
                "--title",
                "Tidepool Field Guide",
                "--branch",
                "Central Branch",
            ],
            [
                "./library-catalog",
                "search",
                "--title",
                "The Cartographer's Lantern",
                "--branch",
                "East Branch",
            ],
        ]
    )
    tidepool_id = sole_id(
        search_results[0], "Tidepool Field Guide in Central Branch"
    )
    lantern_id = sole_id(
        search_results[1], "The Cartographer's Lantern in East Branch"
    )
    concurrent_action(
        [
            ["./library-catalog", "get", "--id", tidepool_id],
            ["./library-catalog", "get", "--id", lantern_id],
        ]
    )


if __name__ == "__main__":
    main()
