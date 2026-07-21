#!/usr/bin/env python3
"""Exercise the reference solution through the real order executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "b5495d4b30b3deb714c93259c0a117fd03ff8b9cf2343d7f05090806f9725a71"


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
    audit = RUNTIME / "order-audit.jsonl"
    audit.unlink(missing_ok=True)

    art_search = RUNTIME / "reference-search-art.json"
    science_search = RUNTIME / "reference-search-science.json"
    art_get = RUNTIME / "reference-get-art.json"
    science_get = RUNTIME / "reference-get-science.json"
    temporary_outputs = (art_search, science_search, art_get, science_get)
    try:
        concurrent_action(
            [
                (
                    [
                        "./order-registry",
                        "search",
                        "--name",
                        "After-School Art Kit Order",
                        "--location",
                        "East Campus",
                    ],
                    art_search,
                ),
                (
                    [
                        "./order-registry",
                        "search",
                        "--name",
                        "Science Lab Refill Order",
                        "--location",
                        "West Campus",
                    ],
                    science_search,
                ),
            ]
        )

        art_id = sole_id(art_search)
        science_id = sole_id(science_search)
        concurrent_action(
            [
                (["./order-registry", "get", "--id", art_id], art_get),
                (["./order-registry", "get", "--id", science_id], science_get),
            ]
        )
    finally:
        for output in temporary_outputs:
            output.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
