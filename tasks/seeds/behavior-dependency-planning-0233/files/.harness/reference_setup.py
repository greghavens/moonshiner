#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine library executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".catalog" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "ddae7ed72c89760ef2ac6d1872c7cbe6d4d226fba5c9e74046a73fdca93e378e"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "library.sqlite3",
        REGISTRY_RUNTIME / "library.sqlite3-shm",
        REGISTRY_RUNTIME / "library.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "library-audit.jsonl",
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
    title_id = matches[0].get("title_id")
    if not isinstance(title_id, str) or not title_id:
        raise RuntimeError(f"reference lookup returned no stable title ID: {label}")
    return title_id


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
                "./library-registry",
                "search",
                "--title",
                "Accessible Exhibit Design",
                "--branch",
                "Museum Branch",
            ],
            [
                "./library-registry",
                "search",
                "--title",
                "Night Sky Field Notes",
                "--branch",
                "North Branch",
            ],
        ]
    )
    accessible_id = sole_id(
        search_results[0], "Accessible Exhibit Design at Museum Branch"
    )
    night_sky_id = sole_id(
        search_results[1], "Night Sky Field Notes at North Branch"
    )
    concurrent_action(
        [
            ["./library-registry", "get", "--id", accessible_id],
            ["./library-registry", "get", "--id", night_sky_id],
        ]
    )


if __name__ == "__main__":
    main()
