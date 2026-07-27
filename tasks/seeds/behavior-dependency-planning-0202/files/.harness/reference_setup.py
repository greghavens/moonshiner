#!/usr/bin/env python3
"""Exercise the reference solution through the genuine trip executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TRIP_RUNTIME = ROOT / ".trips" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "e342ea0880e221b04a0afb07e5974357552586ee99c53ac04cd2ef3d3e89ef34"


def reset_generated_state() -> None:
    for path in (
        TRIP_RUNTIME / "trips.sqlite3",
        TRIP_RUNTIME / "trips.sqlite3-shm",
        TRIP_RUNTIME / "trips.sqlite3-wal",
        TRIP_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "trip-audit.jsonl",
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
                f"reference trip operation failed: {stderr.decode().strip()}"
            )
        results.append(json.loads(stdout))
    return results


def sole_id(payload: dict, label: str) -> str:
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference search did not resolve uniquely: {label}")
    stable_id = matches[0].get("stable_id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError(f"reference search returned no stable ID: {label}")
    return stable_id


def complete_record(payload: dict, stable_id: str, label: str) -> dict:
    record = payload.get("record")
    if not isinstance(record, dict) or record.get("stable_id") != stable_id:
        raise RuntimeError(f"reference retrieval was not complete: {label}")
    for field in ("name", "location", "status", "date"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise RuntimeError(f"reference retrieval lacks {field}: {label}")
    return record


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    searches = concurrent_action(
        [
            [
                "./trip-records",
                "search",
                "--name",
                "Lisbon site visit",
                "--location",
                "Lisbon",
            ],
            [
                "./trip-records",
                "search",
                "--name",
                "Montréal policy summit",
                "--location",
                "Montréal",
            ],
        ]
    )
    lisbon_id = sole_id(searches[0], "Lisbon site visit in Lisbon")
    montreal_id = sole_id(searches[1], "Montréal policy summit in Montréal")
    retrieved = concurrent_action(
        [
            ["./trip-records", "get", "--id", lisbon_id],
            ["./trip-records", "get", "--id", montreal_id],
        ]
    )
    complete_record(retrieved[0], lisbon_id, "Lisbon site visit in Lisbon")
    complete_record(retrieved[1], montreal_id, "Montréal policy summit in Montréal")


if __name__ == "__main__":
    main()
