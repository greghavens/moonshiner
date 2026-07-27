#!/usr/bin/env python3
"""Exercise the reference solution through the genuine claim-desk executables."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".protected" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "c624fa34adbbae320f6bfb7eb2b5cb9617a7216962e5bc86727975aae91eaee7"


def concurrent_action(jobs: list[tuple[str, Path]]) -> None:
    streams = []
    processes = []
    try:
        for command, output_path in jobs:
            stream = output_path.open("w", encoding="utf-8")
            streams.append(stream)
            processes.append(
                subprocess.Popen(
                    ["bash", "-c", command],
                    cwd=ROOT,
                    stdout=stream,
                    start_new_session=True,
                )
            )
        codes = [process.wait() for process in processes]
        if any(code != 0 for code in codes):
            failing_code = next(code for code in codes if code != 0)
            raise subprocess.CalledProcessError(failing_code, jobs)
    finally:
        for stream in streams:
            stream.close()


def sole_id(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference search did not resolve uniquely: {path.name}")
    stable_id = matches[0].get("id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError(f"reference search returned no stable ID: {path.name}")
    return stable_id


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    audit = RUNTIME / "claim-audit.jsonl"
    audit.unlink(missing_ok=True)

    fleet_search = RUNTIME / "reference-search-fleet.json"
    property_search = RUNTIME / "reference-search-property.json"
    concurrent_action(
        [
            (
                "./fleet-desk search --title "
                + shlex.quote("Collision claim — shuttle 30"),
                fleet_search,
            ),
            (
                "./property-desk search --title "
                + shlex.quote("Storm claim — community garden"),
                property_search,
            ),
        ]
    )

    fleet_id = sole_id(fleet_search)
    property_id = sole_id(property_search)
    concurrent_action(
        [
            (
                "./fleet-desk get --id " + shlex.quote(fleet_id),
                RUNTIME / "reference-get-fleet.json",
            ),
            (
                "./property-desk get --id " + shlex.quote(property_id),
                RUNTIME / "reference-get-property.json",
            ),
        ]
    )


if __name__ == "__main__":
    main()
