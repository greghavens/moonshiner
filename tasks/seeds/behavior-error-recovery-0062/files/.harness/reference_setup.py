#!/usr/bin/env python3
"""Exercise the reference recovery through the genuine availability client."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SERVICE_RUNTIME = ROOT / ".travel" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "d50e64a94d3ce6d17dac7bbcab07d23c9ded2372f05109ba52e4981c741c4a2a"
TARGETS = (
    ("Lisbon site visit", "Lisbon", "2026-08-27"),
    ("Montréal policy summit", "Montréal", "2026-08-27"),
)


def reset_generated_state() -> None:
    for path in (
        SERVICE_RUNTIME / "trips.sqlite3",
        SERVICE_RUNTIME / "trips.sqlite3-shm",
        SERVICE_RUNTIME / "trips.sqlite3-wal",
        SERVICE_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "availability-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def command_for(target: tuple[str, str, str]) -> list[str]:
    name, location, trip_date = target
    return [
        "./trip-availability",
        "check",
        "--name",
        name,
        "--location",
        location,
        "--date",
        trip_date,
    ]


def parse_payload(raw: bytes) -> dict:
    value = json.loads(raw.decode())
    if not isinstance(value, dict):
        raise RuntimeError("availability command returned a non-object")
    return value


def initial_action() -> list[tuple[int, dict]]:
    processes = [
        subprocess.Popen(
            command_for(target),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        for target in TARGETS
    ]
    results: list[tuple[int, dict]] = []
    for process in processes:
        stdout, stderr = process.communicate()
        payload = parse_payload(stdout if process.returncode == 0 else stderr)
        results.append((process.returncode, payload))
    return results


def retry_once(target: tuple[str, str, str]) -> tuple[int, dict]:
    completed = subprocess.run(
        command_for(target),
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        check=False,
    )
    payload = parse_payload(
        completed.stdout if completed.returncode == 0 else completed.stderr
    )
    return completed.returncode, payload


def availability_from(result: tuple[int, dict]) -> str | None:
    return_code, payload = result
    value = payload.get("availability")
    return value if return_code == 0 and isinstance(value, str) else None


def is_transient(result: tuple[int, dict]) -> bool:
    return_code, payload = result
    error = payload.get("error")
    return (
        return_code != 0
        and isinstance(error, dict)
        and error.get("kind") == "transient"
    )


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    results = initial_action()
    values = [availability_from(result) for result in results]

    for index, result in enumerate(results):
        if is_transient(result):
            values[index] = availability_from(retry_once(TARGETS[index]))

    labels = ("Lisbon site visit", "Montréal policy summit")
    lines = [
        f"{label}: {value if value is not None else 'unknown'}"
        for label, value in zip(labels, values, strict=True)
    ]
    (ROOT / "availability_report.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
