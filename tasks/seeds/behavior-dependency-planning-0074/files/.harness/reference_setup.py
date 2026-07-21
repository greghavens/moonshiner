#!/usr/bin/env python3
"""Exercise the reference solution through the real expense executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "fd04051439e4a6ed2d1395881eb94d0b74b9e0142c12b42249fe5ac455130115"


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
        raise RuntimeError(f"reference branch did not resolve uniquely: {path.name}")
    stable_id = matches[0].get("stable_id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError(f"reference branch returned no stable ID: {path.name}")
    return stable_id


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    audit = RUNTIME / "expense-audit.jsonl"
    audit.unlink(missing_ok=True)

    first = RUNTIME / "reference-search-first.json"
    second = RUNTIME / "reference-search-second.json"
    concurrent_action(
        [
            (
                [
                    "./expense-records",
                    "search",
                    "--name",
                    "Conference Captioning Service",
                    "--location",
                    "Accessibility",
                ],
                first,
            ),
            (
                [
                    "./expense-records",
                    "search",
                    "--name",
                    "Archive Courier Fee",
                    "--location",
                    "Library Services",
                ],
                second,
            ),
        ]
    )

    first_id = sole_id(first)
    second_id = sole_id(second)
    concurrent_action(
        [
            (
                ["./expense-records", "get", "--id", first_id],
                RUNTIME / "reference-get-first.json",
            ),
            (
                ["./expense-records", "get", "--id", second_id],
                RUNTIME / "reference-get-second.json",
            ),
        ]
    )


if __name__ == "__main__":
    main()
