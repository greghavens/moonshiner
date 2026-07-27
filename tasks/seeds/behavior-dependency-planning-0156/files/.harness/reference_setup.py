#!/usr/bin/env python3
"""Exercise the reference path through the genuine claims executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".claims" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "150f11291d0b1e03637c3e0a39691157f02bedceb245d3cbed5b68a969734e7c"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "claims.sqlite3",
        REGISTRY_RUNTIME / "claims.sqlite3-shm",
        REGISTRY_RUNTIME / "claims.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "claims-audit.jsonl",
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
                f"reference claims operation failed: {stderr.decode().strip()}"
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
            ["./insurance-claims", "get", "--id", "ins-256"],
            ["./insurance-claims", "get", "--id", "ins-656"],
        ]
    )
    smoke = complete_record(retrieved[0], "ins-256", "Kitchen smoke claim")
    debris = complete_record(retrieved[1], "ins-656", "Storm debris claim")

    updates: list[list[str]] = []
    if smoke["status"] == "under-review":
        updates.append(
            [
                "./insurance-claims",
                "update",
                "--id",
                smoke["stable_id"],
                "--status",
                "approved",
            ]
        )
    if debris["status"] == "documents-needed":
        updates.append(
            [
                "./insurance-claims",
                "update",
                "--id",
                debris["stable_id"],
                "--status",
                "under-review",
            ]
        )
    if len(updates) != 2:
        raise RuntimeError("reference preconditions were not both satisfied")

    changed = concurrent_action(updates)
    final_smoke = complete_record(changed[0], "ins-256", "Kitchen smoke claim")
    final_debris = complete_record(changed[1], "ins-656", "Storm debris claim")
    if (
        final_smoke["status"] != "approved"
        or final_debris["status"] != "under-review"
    ):
        raise RuntimeError("reference updates did not return the requested states")


if __name__ == "__main__":
    main()
