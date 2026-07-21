#!/usr/bin/env python3
"""Exercise the reference solution through the real reservation executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "42265b2a13bd19da3bb3a8567aea2b0f98e69a65dad5ec87699e194d5e93ffe1"


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
                next(code for code in codes if code), jobs
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
    audit = RUNTIME / "reservation-audit.jsonl"
    audit.unlink(missing_ok=True)

    seabird_search = RUNTIME / "reference-search-seabird.json"
    redwood_search = RUNTIME / "reference-search-redwood.json"
    concurrent_action(
        [
            (
                [
                    "./reservation-registry",
                    "search",
                    "--name",
                    "Seabird Conference Block",
                    "--location",
                    "San Diego",
                ],
                seabird_search,
            ),
            (
                [
                    "./reservation-registry",
                    "search",
                    "--name",
                    "Redwood Board Retreat",
                    "--location",
                    "Sacramento",
                ],
                redwood_search,
            ),
        ]
    )

    seabird_id = sole_id(seabird_search)
    redwood_id = sole_id(redwood_search)
    concurrent_action(
        [
            (
                ["./reservation-registry", "get", "--id", seabird_id],
                RUNTIME / "reference-get-seabird.json",
            ),
            (
                ["./reservation-registry", "get", "--id", redwood_id],
                RUNTIME / "reference-get-redwood.json",
            ),
        ]
    )


if __name__ == "__main__":
    main()
