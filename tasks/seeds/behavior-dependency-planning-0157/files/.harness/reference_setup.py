#!/usr/bin/env python3
"""Exercise the reference path through the genuine facilities executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".facilities" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "95e9f17a975f6e0eb7611ea6347700ffff0090dfcd911c2f1532803ff0e0263c"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "facilities.sqlite3",
        REGISTRY_RUNTIME / "facilities.sqlite3-shm",
        REGISTRY_RUNTIME / "facilities.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "facilities-audit.jsonl",
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
                f"reference facilities operation failed: {stderr.decode().strip()}"
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
            ["./facilities-records", "get", "--id", "fac-257"],
            ["./facilities-records", "get", "--id", "fac-657"],
        ]
    )
    archive = complete_record(
        retrieved[0], "fac-257", "Archive humidity check"
    )
    garden = complete_record(
        retrieved[1], "fac-657", "Rooftop garden access request"
    )

    updates: list[list[str]] = []
    if archive["status"] == "assigned":
        updates.append(
            [
                "./facilities-records",
                "update",
                "--id",
                archive["stable_id"],
                "--if-status",
                archive["status"],
                "--status",
                "completed",
            ]
        )
    if garden["status"] == "queued":
        updates.append(
            [
                "./facilities-records",
                "update",
                "--id",
                garden["stable_id"],
                "--if-status",
                garden["status"],
                "--status",
                "assigned",
            ]
        )
    if len(updates) != 2:
        raise RuntimeError("reference preconditions were not both satisfied")

    changed = concurrent_action(updates)
    final_archive = complete_record(
        changed[0], "fac-257", "Archive humidity check"
    )
    final_garden = complete_record(
        changed[1], "fac-657", "Rooftop garden access request"
    )
    if (
        changed[0].get("updated") != 1
        or changed[1].get("updated") != 1
        or final_archive["status"] != "completed"
        or final_garden["status"] != "assigned"
    ):
        raise RuntimeError("reference updates did not return the requested states")


if __name__ == "__main__":
    main()
