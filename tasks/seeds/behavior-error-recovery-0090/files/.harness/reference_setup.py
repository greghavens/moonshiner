#!/usr/bin/env python3
"""Exercise the reference behavior through the real project executable."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "79b4b9687d36c4a4277d4af7088e359f403c3b6110dde058a3fe76ddf0098762"

WATER_COMMAND = [
    "./project-registry",
    "availability",
    "--name",
    "Water sampling protocol",
    "--location",
    "River Program",
    "--date",
    "2026-09-24",
]
DONOR_COMMAND = [
    "./project-registry",
    "availability",
    "--name",
    "Donor database cleanup",
    "--location",
    "Museum Program",
    "--date",
    "2026-09-24",
]


def launch_initial() -> list[tuple[list[str], int]]:
    jobs = [
        (WATER_COMMAND, RUNTIME / "reference-water-initial.json"),
        (DONOR_COMMAND, RUNTIME / "reference-donor-initial.json"),
    ]
    running = []
    streams = []
    try:
        for command, output_path in jobs:
            stream = output_path.open("w", encoding="utf-8")
            streams.append(stream)
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            running.append((command, process))
        return [(command, process.wait()) for command, process in running]
    finally:
        for stream in streams:
            stream.close()


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    for path in RUNTIME.iterdir():
        if path.is_file():
            path.unlink()

    outcomes = launch_initial()
    failed = [command for command, code in outcomes if code == 75]
    succeeded = [command for command, code in outcomes if code == 0]
    if len(failed) != 1 or len(succeeded) != 1:
        raise RuntimeError(f"reference initial outcomes were not one success and one transient: {outcomes}")

    retry_output = RUNTIME / "reference-retry.json"
    with retry_output.open("w", encoding="utf-8") as stream:
        retry = subprocess.run(
            failed[0],
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            check=False,
        )
    if retry.returncode != 0:
        raise subprocess.CalledProcessError(retry.returncode, failed[0])


if __name__ == "__main__":
    main()
