#!/usr/bin/env python3
"""Exercise the reference path through the genuine registry executable."""

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


def complete_record(payload: dict, stable_id: str, name: str) -> dict:
    record = payload.get("record")
    if not isinstance(record, dict):
        raise RuntimeError(f"reference retrieval returned no record: {stable_id}")
    if record.get("stable_id") != stable_id or record.get("name") != name:
        raise RuntimeError(f"reference retrieval returned wrong record: {stable_id}")
    if not isinstance(record.get("status"), str):
        raise RuntimeError(f"reference retrieval returned no status: {stable_id}")
    return record


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    retrieved = concurrent_action(
        [
            ["./expense-registry", "get", "--id", "exp-254"],
            ["./expense-registry", "get", "--id", "exp-654"],
        ]
    )
    printing = complete_record(
        retrieved[0], "exp-254", "Community printing invoice"
    )
    breakfast = complete_record(
        retrieved[1], "exp-654", "Mentor breakfast receipt"
    )

    updates: list[list[str]] = []
    if printing["status"] == "approved":
        updates.append(
            [
                "./expense-registry",
                "update",
                "--id",
                printing["stable_id"],
                "--status",
                "audit-cleared",
            ]
        )
    if breakfast["status"] == "submitted":
        updates.append(
            [
                "./expense-registry",
                "update",
                "--id",
                breakfast["stable_id"],
                "--status",
                "approved",
            ]
        )
    if len(updates) != 2:
        raise RuntimeError("reference preconditions were not both satisfied")

    changed = concurrent_action(updates)
    final_printing = complete_record(
        changed[0], "exp-254", "Community printing invoice"
    )
    final_breakfast = complete_record(
        changed[1], "exp-654", "Mentor breakfast receipt"
    )
    if (
        final_printing["status"] != "audit-cleared"
        or final_breakfast["status"] != "approved"
    ):
        raise RuntimeError("reference updates did not return the requested states")


if __name__ == "__main__":
    main()
