#!/usr/bin/env python3
"""Produce genuine reference execution evidence through clinic-records."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"


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
        return_codes = [process.wait() for process in processes]
        if any(code != 0 for code in return_codes):
            raise RuntimeError(f"reference operation failed: {return_codes}")
    finally:
        for stream in streams:
            stream.close()


def unique_id(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference search was not unique: {path.name}")
    record_id = matches[0].get("id")
    if not isinstance(record_id, str) or not record_id:
        raise RuntimeError(f"reference search returned no stable ID: {path.name}")
    return record_id


def main() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "clinic-audit.jsonl").unlink(missing_ok=True)

    with (RUNTIME / "reference-help.txt").open("w", encoding="utf-8") as stream:
        subprocess.run(
            ["./clinic-records", "--help"],
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
                    "./clinic-records",
                    "search",
                    "--name",
                    "Vaccination visit — Priya Shah",
                    "--location",
                    "Maple Clinic",
                ],
                first_search,
            ),
            (
                [
                    "./clinic-records",
                    "search",
                    "--name",
                    "Physical therapy intake — Luis Ortiz",
                    "--location",
                    "River Clinic",
                ],
                second_search,
            ),
        ]
    )

    first_id = unique_id(first_search)
    second_id = unique_id(second_search)
    concurrent_action(
        [
            (
                ["./clinic-records", "get", "--id", first_id],
                RUNTIME / "reference-get-first.json",
            ),
            (
                ["./clinic-records", "get", "--id", second_id],
                RUNTIME / "reference-get-second.json",
            ),
        ]
    )


if __name__ == "__main__":
    main()
