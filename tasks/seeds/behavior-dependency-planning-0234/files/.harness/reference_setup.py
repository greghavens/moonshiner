#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine expense executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".expenses" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "f201a29e19bb939e950cf0fc854d648a839268d0dabd4de9a75509dbeb777592"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "expenses.sqlite3",
        REGISTRY_RUNTIME / "expenses.sqlite3-shm",
        REGISTRY_RUNTIME / "expenses.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "expense-audit.jsonl",
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
    expense_id = matches[0].get("expense_id")
    if not isinstance(expense_id, str) or not expense_id:
        raise RuntimeError(f"reference lookup returned no stable expense ID: {label}")
    return expense_id


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
                "./expense-registry",
                "search",
                "--description",
                "Portland supplies — volunteer fair",
                "--city",
                "Portland",
            ],
            [
                "./expense-registry",
                "search",
                "--description",
                "Raleigh taxi — museum loan",
                "--city",
                "Raleigh",
            ],
        ]
    )
    portland_id = sole_id(
        search_results[0], "Portland supplies — volunteer fair in Portland"
    )
    raleigh_id = sole_id(
        search_results[1], "Raleigh taxi — museum loan in Raleigh"
    )
    concurrent_action(
        [
            ["./expense-registry", "get", "--id", portland_id],
            ["./expense-registry", "get", "--id", raleigh_id],
        ]
    )


if __name__ == "__main__":
    main()
