#!/usr/bin/env python3
"""Exercise the reference solution through the genuine ledger executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LEDGER_RUNTIME = ROOT / ".expense_data" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
REPORT_PATH = ROOT / "expense-review.md"
REPORT_DIGEST = "464180df5de8252ea6f588152e1fc562a198b914cdc49f1bb978a72f2052ccca"


def reset_generated_state() -> None:
    for path in (
        LEDGER_RUNTIME / "ledger.sqlite3",
        LEDGER_RUNTIME / "ledger.sqlite3-shm",
        LEDGER_RUNTIME / "ledger.sqlite3-wal",
        LEDGER_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "ledger-audit.jsonl",
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
                f"reference ledger operation failed: {stderr.decode().strip()}"
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
                "./expense-ledger",
                "search",
                "--description",
                "Chicago rail fare — outreach trip",
                "--city",
                "Chicago",
            ],
            [
                "./expense-ledger",
                "search",
                "--description",
                "Boston team lunch — budget workshop",
                "--city",
                "Boston",
            ],
        ]
    )
    chicago_id = sole_id(
        search_results[0], "Chicago rail fare — outreach trip in Chicago"
    )
    boston_id = sole_id(
        search_results[1], "Boston team lunch — budget workshop in Boston"
    )
    concurrent_action(
        [
            ["./expense-ledger", "get", "--id", chicago_id],
            ["./expense-ledger", "get", "--id", boston_id],
        ]
    )


if __name__ == "__main__":
    main()
