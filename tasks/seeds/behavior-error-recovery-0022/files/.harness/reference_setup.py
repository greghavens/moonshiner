#!/usr/bin/env python3
"""Exercise the reference solution through the genuine travel executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "708acc582fa65a87deb9b942e30a54d4ab677d3708aecf731800af088e9b561a"

KYOTO = [
    "./travel-availability",
    "check",
    "--name",
    "Kyoto visit",
    "--date",
    "2026-09-13",
    "--location",
    "Kyoto",
]
MONTREAL = [
    "./travel-availability",
    "check",
    "--name",
    "Montreal visit",
    "--date",
    "2026-09-13",
    "--location",
    "Montreal",
]


def decode_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "travel-audit.jsonl").unlink(missing_ok=True)

    stdout_paths = [RUNTIME / "reference-kyoto.json", RUNTIME / "reference-montreal.json"]
    stderr_paths = [RUNTIME / "reference-kyoto.err", RUNTIME / "reference-montreal.err"]
    streams = []
    processes = []
    try:
        for command, stdout_path, stderr_path in zip(
            (KYOTO, MONTREAL), stdout_paths, stderr_paths
        ):
            stdout_stream = stdout_path.open("w", encoding="utf-8")
            stderr_stream = stderr_path.open("w", encoding="utf-8")
            streams.extend((stdout_stream, stderr_stream))
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    start_new_session=True,
                )
            )
        codes = [process.wait() for process in processes]
    finally:
        for stream in streams:
            stream.close()

    if codes != [0, 75]:
        raise RuntimeError(f"unexpected initial reference exit codes: {codes}")
    kyoto = decode_json(stdout_paths[0])
    if set(kyoto) != {"name", "date", "location", "availability"}:
        raise RuntimeError("Kyoto reference lookup returned an invalid response")
    transient = decode_json(stderr_paths[1])
    if transient.get("retryable") is not True:
        raise RuntimeError("Montreal reference lookup was not retryable")

    retry = subprocess.run(
        MONTREAL,
        cwd=ROOT,
        capture_output=True,
        text=True,
        start_new_session=True,
        check=True,
    )
    montreal = json.loads(retry.stdout)
    if set(montreal) != {"name", "date", "location", "availability"}:
        raise RuntimeError("Montreal reference retry returned an invalid response")


if __name__ == "__main__":
    main()
