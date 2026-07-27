#!/usr/bin/env python3
"""Exercise the reference solution through genuine ledger processes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LEDGER_RUNTIME = ROOT / ".expenses" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "0652e5da8207cfaa39d45739b71a863daf090b7b3dafa1c0e0eebb0d046c5196"


def reset_generated_state() -> None:
    for path in (
        LEDGER_RUNTIME / "ledger.sqlite3",
        LEDGER_RUNTIME / "ledger.sqlite3-shm",
        LEDGER_RUNTIME / "ledger.sqlite3-wal",
        LEDGER_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "expense-availability-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def decode_payload(stdout: bytes, stderr: bytes) -> dict:
    raw = stdout if stdout.strip() else stderr
    return json.loads(raw.decode().strip())


def concurrent_initial_action() -> list[tuple[int, dict]]:
    commands = [
        [
            "./expense-ledger",
            "availability",
            "--name",
            "Denver lodging — policy summit",
            "--location",
            "Denver",
            "--date",
            "2026-09-04",
        ],
        [
            "./expense-ledger",
            "availability",
            "--name",
            "Tucson mileage — field sampling",
            "--location",
            "Tucson",
            "--date",
            "2026-09-04",
        ],
    ]
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
    if initial[0] != (75, {"error": "temporary_unavailable", "transient": True}):
        raise RuntimeError("reference Denver branch did not transiently fail")
    if initial[1][0] != 0 or initial[1][1].get("available") is not True:
        raise RuntimeError("reference Tucson branch did not return availability")

    retry = subprocess.run(
        [
            "./expense-ledger",
            "availability",
            "--name",
            "Denver lodging — policy summit",
            "--location",
            "Denver",
            "--date",
            "2026-09-04",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        start_new_session=True,
    )
    retry_payload = decode_payload(retry.stdout, retry.stderr)
    if retry.returncode != 0 or retry_payload.get("available") is not False:
        raise RuntimeError("reference Denver retry did not return availability")


if __name__ == "__main__":
    main()
