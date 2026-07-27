#!/usr/bin/env python3
"""Exercise the reference answer through the genuine course executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
COURSE_RUNTIME = ROOT / ".courses" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
REPORT_PATH = ROOT / "course-handoff.md"
REPORT_DIGEST = "7d5f878578eb85c754df166594bb80ee1210517eded1387127e6c99c1ec5e5de"


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
    stable_id = matches[0].get("stable_id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError(f"reference lookup returned no stable ID: {label}")
    return stable_id


def main() -> None:
    if (
        not REPORT_PATH.is_file()
        or hashlib.sha256(REPORT_PATH.read_bytes()).hexdigest() != REPORT_DIGEST
    ):
        return

    reset_generated_state()
    search_results = concurrent_action(
        [
            [
                "./course-registry",
                "search",
                "--title",
                "Wetland field methods",
                "--campus",
                "River Campus",
            ],
            [
                "./course-registry",
                "search",
                "--title",
                "Introductory American Sign Language",
                "--campus",
                "Central Campus",
            ],
        ]
    )
    wetland_id = sole_id(
        search_results[0], "Wetland field methods in River Campus"
    )
    asl_id = sole_id(
        search_results[1],
        "Introductory American Sign Language in Central Campus",
    )
    concurrent_action(
        [
            ["./course-registry", "get", "--id", wetland_id],
            ["./course-registry", "get", "--id", asl_id],
        ]
    )


if __name__ == "__main__":
    main()
