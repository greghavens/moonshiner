#!/usr/bin/env python3
"""Exercise the reference solution through the real trip executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "fb50eb00c843f662b35d1b394f0681310156169d4d88273d53db7fa0bda9a9e2"


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
            raise subprocess.CalledProcessError(
                next(code for code in codes if code), "concurrent registry action"
            )
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
    audit = RUNTIME / "trip-audit.jsonl"
    audit.unlink(missing_ok=True)

    seoul = RUNTIME / "reference-search-seoul.json"
    taipei = RUNTIME / "reference-search-taipei.json"
    concurrent_action(
        [
            (
                [
                    "./trip-registry",
                    "search",
                    "--name",
                    "Seoul Trade Delegation",
                    "--location",
                    "Seoul",
                ],
                seoul,
            ),
            (
                [
                    "./trip-registry",
                    "search",
                    "--name",
                    "Taipei Standards Forum",
                    "--location",
                    "Taipei",
                ],
                taipei,
            ),
        ]
    )

    seoul_id = sole_id(seoul)
    taipei_id = sole_id(taipei)
    concurrent_action(
        [
            (
                ["./trip-registry", "get", "--id", seoul_id],
                RUNTIME / "reference-get-seoul.json",
            ),
            (
                ["./trip-registry", "get", "--id", taipei_id],
                RUNTIME / "reference-get-taipei.json",
            ),
        ]
    )


if __name__ == "__main__":
    main()
