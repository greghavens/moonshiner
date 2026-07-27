#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine order executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".order-runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "aacac999ccbfc481c4585271541ed68e81e956402011396aeea604ee5e39d6d6"

OFFICE = [
    "./order-availability",
    "check",
    "--item",
    "Office order",
    "--date",
    "2026-09-17",
    "--location",
    "Boise",
]
GIFT = [
    "./order-availability",
    "check",
    "--item",
    "Gift order",
    "--date",
    "2026-09-17",
    "--location",
    "Phoenix",
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

    stdout_paths = [RUNTIME / "reference-office.json", RUNTIME / "reference-gift.json"]
    stderr_paths = [RUNTIME / "reference-office.err", RUNTIME / "reference-gift.err"]
    streams = []
    processes = []
    try:
        for command, stdout_path, stderr_path in zip(
            (OFFICE, GIFT), stdout_paths, stderr_paths
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
    office = decode_json(stdout_paths[0])
    if set(office) != {"item", "date", "location", "availability"}:
        raise RuntimeError("Office order reference lookup returned an invalid response")
    transient = decode_json(stderr_paths[1])
    if transient.get("retryable") is not True:
        raise RuntimeError("Gift order reference lookup was not retryable")

    retry = subprocess.run(
        GIFT,
        cwd=ROOT,
        capture_output=True,
        text=True,
        start_new_session=True,
        check=True,
    )
    gift = json.loads(retry.stdout)
    if set(gift) != {"item", "date", "location", "availability"}:
        raise RuntimeError("Gift order reference retry returned an invalid response")


if __name__ == "__main__":
    main()
