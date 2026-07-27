#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine appointment executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".protected" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "e830e21e262a3b3fb8c82dfd5037285a9fd7742cb99f2900cbca807c511acfff"


def concurrent_action(jobs: list[tuple[list[str], Path]]) -> None:
    streams = []
    processes = []
    try:
        for command, output_path in jobs:
            stream = output_path.open("w", encoding="utf-8")
            streams.append(stream)
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=stream,
                    start_new_session=True,
                )
            )
        codes = [process.wait() for process in processes]
        if any(code != 0 for code in codes):
            raise subprocess.CalledProcessError(next(code for code in codes if code), jobs)
    finally:
        for stream in streams:
            stream.close()


def sole_id(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference search did not resolve uniquely: {path.name}")
    record_id = matches[0].get("id")
    if not isinstance(record_id, str) or not record_id:
        raise RuntimeError(f"reference search returned no stable ID: {path.name}")
    return record_id


def record(path: Path, expected_id: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    value = payload.get("record")
    if not isinstance(value, dict) or value.get("id") != expected_id:
        raise RuntimeError(f"reference retrieval was invalid: {path.name}")
    return value


def main() -> None:
    if not MARKER.is_file() or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST:
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "appointment-audit.jsonl").unlink(missing_ok=True)
    with (RUNTIME / "reference-help.txt").open("w", encoding="utf-8") as stream:
        subprocess.run(
            ["./appointmentctl", "--help"],
            cwd=ROOT,
            stdout=stream,
            check=True,
            start_new_session=True,
        )
    first_search = RUNTIME / "reference-search-first.json"
    second_search = RUNTIME / "reference-search-second.json"
    concurrent_action(
        [
            (
                [
                    "./appointmentctl",
                    "search",
                    "--name",
                    "Vision screening — Erin Brooks",
                    "--location",
                    "North Clinic",
                ],
                first_search,
            ),
            (
                [
                    "./appointmentctl",
                    "search",
                    "--name",
                    "Nutrition consult — Sam Adeyemi",
                    "--location",
                    "South Clinic",
                ],
                second_search,
            ),
        ]
    )

    first_id = sole_id(first_search)
    second_id = sole_id(second_search)
    first_get = RUNTIME / "reference-get-first.json"
    second_get = RUNTIME / "reference-get-second.json"
    concurrent_action(
        [
            (["./appointmentctl", "get", "--id", first_id], first_get),
            (["./appointmentctl", "get", "--id", second_id], second_get),
        ]
    )

    first = record(first_get, first_id)
    second = record(second_get, second_id)
    report = (
        "| Appointment | ID | Date | Status |\n"
        "| --- | --- | --- | --- |\n"
        f"| {first['name']} | {first['id']} | {first['date']} | {first['status']} |\n"
        f"| {second['name']} | {second['id']} | {second['date']} | {second['status']} |\n"
        "\n"
        f"Comparison: {first['name']} is {first['status']} for {first['date']}; "
        f"{second['name']} is {second['status']} for {second['date']}.\n"
    )
    (ROOT / "appointment_audit.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
