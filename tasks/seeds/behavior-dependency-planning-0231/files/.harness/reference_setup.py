#!/usr/bin/env python3
"""Exercise the reference solution through the genuine candidate executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".candidates" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "6fdfa388a312a12f00930ce855615bf16b9fe281f0c2036e40cfada32d49363b"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "candidates.sqlite3",
        REGISTRY_RUNTIME / "candidates.sqlite3-shm",
        REGISTRY_RUNTIME / "candidates.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "candidate-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def concurrent_action(commands: list[list[str]]) -> list[dict]:
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        for command in commands
    ]
    results: list[dict] = []
    for process in processes:
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"reference registry operation failed: {stderr.decode().strip()}"
            )
        results.append(json.loads(stdout))
    return results


def sole_id(payload: dict, label: str) -> str:
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference lookup did not resolve uniquely: {label}")
    candidate_id = matches[0].get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise RuntimeError(f"reference lookup returned no candidate ID: {label}")
    return candidate_id


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    search_results = concurrent_action(
        [
            [
                "./candidate-registry",
                "search",
                "--name",
                "Avery Jones",
                "--role",
                "Clinic Scheduler",
                "--department",
                "Health Services",
            ],
            [
                "./candidate-registry",
                "search",
                "--name",
                "Jordan Kim",
                "--role",
                "Museum Educator",
                "--department",
                "Education",
            ],
        ]
    )
    avery_id = sole_id(
        search_results[0], "Avery Jones — Clinic Scheduler in Health Services"
    )
    jordan_id = sole_id(
        search_results[1], "Jordan Kim — Museum Educator in Education"
    )
    concurrent_action(
        [
            ["./candidate-registry", "get", "--id", avery_id],
            ["./candidate-registry", "get", "--id", jordan_id],
        ]
    )


if __name__ == "__main__":
    main()
