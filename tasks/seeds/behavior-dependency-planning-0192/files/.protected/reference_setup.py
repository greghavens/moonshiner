#!/usr/bin/env python3
"""Exercise the reference answer through genuine coursectl processes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
COURSE_RUNTIME = ROOT / ".course_data" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "4e78b6222302227b224fd67110b0446af1bea12aed2d08b0ca29f8b2516d65b5"


def reset_generated_state() -> None:
    for path in (
        COURSE_RUNTIME / "courses.sqlite3",
        COURSE_RUNTIME / "courses.sqlite3-shm",
        COURSE_RUNTIME / "courses.sqlite3-wal",
        COURSE_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "course-audit.jsonl",
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
        stdout, stderr = process.communicate(timeout=15)
        if process.returncode != 0:
            raise RuntimeError(
                f"reference course operation failed: {stderr.decode().strip()}"
            )
        results.append(json.loads(stdout))
    return results


def sole_id(payload: dict, label: str) -> str:
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference search did not resolve uniquely: {label}")
    stable_id = matches[0].get("stable_id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError(f"reference search returned no stable ID: {label}")
    return stable_id


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    searches = concurrent_action(
        [
            [
                "./coursectl",
                "search",
                "--name",
                "Microeconomics evening seminar",
                "--campus",
                "Downtown Campus",
            ],
            [
                "./coursectl",
                "search",
                "--name",
                "Modern history survey",
                "--campus",
                "North Campus",
            ],
        ]
    )
    first_id = sole_id(searches[0], "Microeconomics evening seminar")
    second_id = sole_id(searches[1], "Modern history survey")
    concurrent_action(
        [
            ["./coursectl", "get", "--id", first_id],
            ["./coursectl", "get", "--id", second_id],
        ]
    )


if __name__ == "__main__":
    main()
