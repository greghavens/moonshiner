#!/usr/bin/env python3
"""Exercise the reference workflow through genuine claimctl processes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CLAIM_RUNTIME = ROOT / ".claim-runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "216a24dee0701cc58a72dec6b90e5d2d22296d0202242022837488c8e7182955"

WATER = (
    "Water damage claim — archive room",
    "Central Office",
    "2026-09-11",
)
LUGGAGE = (
    "Lost baggage claim — conference trip",
    "Travel Desk",
    "2026-09-11",
)


def command_for(query: tuple[str, str, str]) -> list[str]:
    name, office, date = query
    return [
        "./claimctl",
        "availability",
        "--name",
        name,
        "--office",
        office,
        "--date",
        date,
    ]


def reset_generated_state() -> None:
    for path in (
        CLAIM_RUNTIME / "claims.sqlite3",
        CLAIM_RUNTIME / "claims.sqlite3-shm",
        CLAIM_RUNTIME / "claims.sqlite3-wal",
        CLAIM_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "claim-availability-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def decode_payload(stdout: bytes, stderr: bytes) -> dict:
    raw = stdout if stdout.strip() else stderr
    return json.loads(raw.decode().strip())


def concurrent_initial_action() -> list[tuple[int, dict]]:
    processes = [
        subprocess.Popen(
            command_for(query),
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        for query in (WATER, LUGGAGE)
    ]
    results: list[tuple[int, dict]] = []
    for process in processes:
        stdout, stderr = process.communicate()
        results.append((process.returncode, decode_payload(stdout, stderr)))
    return results


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    initial = concurrent_initial_action()
    if initial[0] != (
        75,
        {
            "error": "temporary_unavailable",
            "retryable": True,
            "transient": True,
        },
    ):
        raise RuntimeError("reference water-damage branch did not transiently fail")
    if initial[1][0] != 0 or initial[1][1].get("available") is not False:
        raise RuntimeError("reference baggage branch did not return availability")

    retry = subprocess.run(
        command_for(WATER),
        cwd=ROOT,
        capture_output=True,
        check=False,
        start_new_session=True,
    )
    retry_payload = decode_payload(retry.stdout, retry.stderr)
    if retry.returncode != 0 or retry_payload.get("available") is not True:
        raise RuntimeError("reference water-damage retry did not return availability")


if __name__ == "__main__":
    main()
