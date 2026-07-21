#!/usr/bin/env python3
"""Exercise the reference solution through the genuine catalog executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CATALOG_RUNTIME = ROOT / ".catalog" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
REFERENCE_REPORT = ROOT / "status_audit.md"
REFERENCE_REPORT_DIGEST = "86641a1d9b5160d6d04fd090cd935c8b1f50f1a992dbcbd0d2935141061895e7"


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
    if (
        not REFERENCE_REPORT.is_file()
        or hashlib.sha256(REFERENCE_REPORT.read_bytes()).hexdigest()
        != REFERENCE_REPORT_DIGEST
    ):
        return

    reset_generated_state()
    search_results = concurrent_action(
        [
            [
                "./course-catalog",
                "search",
                "--name",
                "Environmental Economics",
                "--campus",
                "Downtown Campus",
            ],
            [
                "./course-catalog",
                "search",
                "--name",
                "Oral History Workshop",
                "--campus",
                "North Campus",
            ],
        ]
    )
    economics_id = sole_id(
        search_results[0], "Environmental Economics in Downtown Campus"
    )
    history_id = sole_id(
        search_results[1], "Oral History Workshop in North Campus"
    )
    concurrent_action(
        [
            ["./course-catalog", "get", "--id", economics_id],
            ["./course-catalog", "get", "--id", history_id],
        ]
    )


if __name__ == "__main__":
    main()
