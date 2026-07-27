#!/usr/bin/env python3
"""Exercise the reference answer through the genuine expense executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EXPENSE_RUNTIME = ROOT / ".expenses" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
REPORT_PATH = ROOT / "expense-review.md"
REPORT_DIGEST = "662ff166ed773b7a2a5f315ba54e97fa162c2779e10965a6ecabff7e4d9b7858"


def reset_generated_state() -> None:
    for path in (
        EXPENSE_RUNTIME / "expenses.sqlite3",
        EXPENSE_RUNTIME / "expenses.sqlite3-shm",
        EXPENSE_RUNTIME / "expenses.sqlite3-wal",
        EXPENSE_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "expense-audit.jsonl",
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
        not REPORT_PATH.is_file()
        or hashlib.sha256(REPORT_PATH.read_bytes()).hexdigest() != REPORT_DIGEST
    ):
        return

    reset_generated_state()
    search_results = concurrent_action(
        [
            [
                "./expense-registry",
                "search",
                "--name",
                "Denver lodging — policy summit",
                "--city",
                "Denver",
            ],
            [
                "./expense-registry",
                "search",
                "--name",
                "Tucson mileage — field sampling",
                "--city",
                "Tucson",
            ],
        ]
    )
    denver_id = sole_id(
        search_results[0], "Denver lodging — policy summit in Denver"
    )
    tucson_id = sole_id(
        search_results[1], "Tucson mileage — field sampling in Tucson"
    )
    concurrent_action(
        [
            ["./expense-registry", "get", "--id", denver_id],
            ["./expense-registry", "get", "--id", tucson_id],
        ]
    )


if __name__ == "__main__":
    main()
