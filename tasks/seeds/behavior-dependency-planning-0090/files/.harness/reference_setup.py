#!/usr/bin/env python3
"""Exercise the reference solution through the real task executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "e7e033e5570b3cfcad062d9c5c78e34d79351e9e7a9d820fcc410bceb16b08f4"


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
    audit = RUNTIME / "task-audit.jsonl"
    audit.unlink(missing_ok=True)

    carrier_search = RUNTIME / "reference-search-carrier.json"
    returns_search = RUNTIME / "reference-search-returns.json"
    concurrent_action(
        [
            (
                [
                    "./task-registry",
                    "search",
                    "--name",
                    "Carrier Scorecard Refresh",
                    "--location",
                    "Logistics Program",
                ],
                carrier_search,
            ),
            (
                [
                    "./task-registry",
                    "search",
                    "--name",
                    "Returns Workflow Pilot",
                    "--location",
                    "Commerce Program",
                ],
                returns_search,
            ),
        ]
    )

    carrier_id = sole_id(carrier_search)
    returns_id = sole_id(returns_search)
    concurrent_action(
        [
            (
                ["./task-registry", "get", "--id", carrier_id],
                RUNTIME / "reference-get-carrier.json",
            ),
            (
                ["./task-registry", "get", "--id", returns_id],
                RUNTIME / "reference-get-returns.json",
            ),
        ]
    )


if __name__ == "__main__":
    main()
