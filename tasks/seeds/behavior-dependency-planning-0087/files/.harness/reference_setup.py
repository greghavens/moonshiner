#!/usr/bin/env python3
"""Exercise the reference solution through the real inventory executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "6087f9df075d4200b778434b7f00730ed6235eb5135e82c9304257f9e220acde"


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
    audit = RUNTIME / "inventory-audit.jsonl"
    reference_audit = RUNTIME / "reference-inventory-audit.jsonl"
    audit.unlink(missing_ok=True)
    reference_audit.unlink(missing_ok=True)

    glaze_search = RUNTIME / "reference-search-glaze.json"
    canvas_search = RUNTIME / "reference-search-canvas.json"
    concurrent_action(
        [
            (
                [
                    "./inventory-registry",
                    "search",
                    "--name",
                    "Ceramic Glaze Set",
                    "--location",
                    "Arts Stockroom",
                ],
                glaze_search,
            ),
            (
                [
                    "./inventory-registry",
                    "search",
                    "--name",
                    "Canvas Panel Case",
                    "--location",
                    "Teaching Studio",
                ],
                canvas_search,
            ),
        ]
    )
    glaze_id = sole_id(glaze_search)
    canvas_id = sole_id(canvas_search)
    concurrent_action(
        [
            (
                ["./inventory-registry", "get", "--id", glaze_id],
                RUNTIME / "reference-get-glaze.json",
            ),
            (
                ["./inventory-registry", "get", "--id", canvas_id],
                RUNTIME / "reference-get-canvas.json",
            ),
        ]
    )
    audit.replace(reference_audit)


if __name__ == "__main__":
    main()
