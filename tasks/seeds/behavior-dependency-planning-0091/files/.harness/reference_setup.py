#!/usr/bin/env python3
"""Exercise the reference solution through the genuine directory executable."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY_RUNTIME = ROOT / ".directory" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
REPORT_PATH = ROOT / "status_audit.md"
EXPECTED_REPORT = (
    "Priya Nair in Analytics has status Active.\n"
    "Mateo Silva in Customer Success has status On leave.\n"
    "The returned statuses differ.\n"
)


def reset_generated_state() -> None:
    for path in (
        DIRECTORY_RUNTIME / "directory.sqlite3",
        DIRECTORY_RUNTIME / "directory.sqlite3-shm",
        DIRECTORY_RUNTIME / "directory.sqlite3-wal",
        DIRECTORY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "directory-audit.jsonl",
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
                f"reference directory operation failed: {stderr.decode().strip()}"
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
        not REPORT_PATH.is_file()
        or REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT
    ):
        return

    reset_generated_state()
    search_results = concurrent_action(
        [
            [
                "./staff-directory",
                "search",
                "--name",
                "Priya Nair",
                "--department",
                "Analytics",
            ],
            [
                "./staff-directory",
                "search",
                "--name",
                "Mateo Silva",
                "--department",
                "Customer Success",
            ],
        ]
    )
    priya_id = sole_id(search_results[0], "Priya Nair in Analytics")
    mateo_id = sole_id(search_results[1], "Mateo Silva in Customer Success")
    concurrent_action(
        [
            ["./staff-directory", "get", "--id", priya_id],
            ["./staff-directory", "get", "--id", mateo_id],
        ]
    )


if __name__ == "__main__":
    main()
