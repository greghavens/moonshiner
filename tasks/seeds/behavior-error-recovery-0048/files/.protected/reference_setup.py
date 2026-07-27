#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine parcel executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".parcel-runtime"
MARKER = ROOT / ".reference_solution"
REPORT = ROOT / "availability-report.txt"
MARKER_DIGEST = "1248859aba6d7116abd8f6791c0c5025506ea4b2c9d1cb1d453ad9d6d5058ed2"

DRIFT = [
    "./parcel-availability",
    "check",
    "--parcel",
    "Parcel Drift",
    "--date",
    "2026-11-21",
    "--location",
    "Portland",
]
AURORA = [
    "./parcel-availability",
    "check",
    "--parcel",
    "Parcel Aurora",
    "--date",
    "2026-11-21",
    "--location",
    "Denver",
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
    (RUNTIME / "execution.jsonl").unlink(missing_ok=True)

    subprocess.run(
        ["./parcel-availability", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        start_new_session=True,
        check=True,
    )

    stdout_paths = [RUNTIME / "reference-drift.json", RUNTIME / "reference-aurora.json"]
    stderr_paths = [RUNTIME / "reference-drift.err", RUNTIME / "reference-aurora.err"]
    streams = []
    processes = []
    try:
        for command, stdout_path, stderr_path in zip(
            (DRIFT, AURORA), stdout_paths, stderr_paths
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
    drift = decode_json(stdout_paths[0])
    if set(drift) != {"parcel", "date", "location", "availability"}:
        raise RuntimeError("Parcel Drift reference check returned an invalid response")
    transient = decode_json(stderr_paths[1])
    if transient.get("retryable") is not True:
        raise RuntimeError("Parcel Aurora reference check was not retryable")

    retry = subprocess.run(
        AURORA,
        cwd=ROOT,
        capture_output=True,
        text=True,
        start_new_session=True,
        check=True,
    )
    aurora = json.loads(retry.stdout)
    if set(aurora) != {"parcel", "date", "location", "availability"}:
        raise RuntimeError("Parcel Aurora reference retry returned an invalid response")

    for path in (*stdout_paths, *stderr_paths):
        path.unlink(missing_ok=True)

    report = (
        f"{drift['parcel']} / {drift['location']}: {drift['availability']}.\n"
        f"{aurora['parcel']} / {aurora['location']}: "
        f"{aurora['availability']} (retried).\n"
    )
    REPORT.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
