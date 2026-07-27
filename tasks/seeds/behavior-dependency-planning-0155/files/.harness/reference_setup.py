#!/usr/bin/env python3
"""Exercise the reference path through the genuine clinic executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SERVICE_RUNTIME = ROOT / ".clinic" / "__pycache__"
HARNESS_RUNTIME = ROOT / ".harness" / "__pycache__"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "285bbf980e9e189d3e76a1a22391137b632a00f73430919b85b7f4e518890cb4"


def reset_generated_state() -> None:
    for path in (
        SERVICE_RUNTIME / "clinic.sqlite3",
        SERVICE_RUNTIME / "clinic.sqlite3-shm",
        SERVICE_RUNTIME / "clinic.sqlite3-wal",
        SERVICE_RUNTIME / "clinic.sqlite3-journal",
        SERVICE_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "clinic-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_help() -> None:
    result = subprocess.run(
        ["./clinic-records", "--help"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        check=False,
    )
    if result.returncode != 0 or b"usage: clinic-records" not in result.stdout:
        raise RuntimeError("reference client help failed")


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
                f"reference clinic operation failed: {stderr.decode().strip()}"
            )
        results.append(json.loads(stdout))
    return results


def complete_record(payload: dict, stable_id: str, title: str, patient: str) -> dict:
    record = payload.get("record")
    if not isinstance(record, dict):
        raise RuntimeError(f"reference retrieval returned no record: {stable_id}")
    if (
        record.get("id") != stable_id
        or record.get("title") != title
        or record.get("patient") != patient
    ):
        raise RuntimeError(f"reference retrieval returned the wrong record: {stable_id}")
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
    run_help()
    retrieved = concurrent_action(
        [
            ["./clinic-records", "get", "--id", "hea-255"],
            ["./clinic-records", "get", "--id", "hea-655"],
        ]
    )
    therapy = complete_record(
        retrieved[0], "hea-255", "Physical therapy", "Alex Green"
    )
    imaging = complete_record(
        retrieved[1], "hea-655", "Imaging appointment", "Casey Bell"
    )

    updates: list[list[str]] = []
    if therapy["status"] == "confirmed":
        updates.append(
            [
                "./clinic-records",
                "update",
                "--id",
                therapy["id"],
                "--if-status",
                therapy["status"],
                "--to-status",
                "completed",
            ]
        )
    if imaging["status"] == "requested":
        updates.append(
            [
                "./clinic-records",
                "update",
                "--id",
                imaging["id"],
                "--if-status",
                imaging["status"],
                "--to-status",
                "confirmed",
            ]
        )
    if len(updates) != 2:
        raise RuntimeError("reference preconditions were not both satisfied")

    changed = concurrent_action(updates)
    final_therapy = complete_record(
        changed[0], "hea-255", "Physical therapy", "Alex Green"
    )
    final_imaging = complete_record(
        changed[1], "hea-655", "Imaging appointment", "Casey Bell"
    )
    if (
        changed[0].get("changed") is not True
        or changed[1].get("changed") is not True
        or final_therapy["status"] != "completed"
        or final_imaging["status"] != "confirmed"
    ):
        raise RuntimeError("reference conditional updates did not succeed")


if __name__ == "__main__":
    main()
