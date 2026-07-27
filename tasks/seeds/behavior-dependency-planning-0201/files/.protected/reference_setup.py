#!/usr/bin/env python3
"""Exercise the reference answer through the genuine meeting executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MEETING_RUNTIME = ROOT / ".meetings" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
REPORT_PATH = ROOT / "meeting-check.md"
REPORT_DIGEST = "f1d7dd21cf709db8f724873d4066dd6bdf9537eea826a4b87e1c44e38668dc59"


def reset_generated_state() -> None:
    for path in (
        MEETING_RUNTIME / "meetings.sqlite3",
        MEETING_RUNTIME / "meetings.sqlite3-shm",
        MEETING_RUNTIME / "meetings.sqlite3-wal",
        MEETING_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "meeting-audit.jsonl",
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
                "./meeting-registry",
                "search",
                "--name",
                "Accessibility Council Check-in",
                "--city",
                "Denver",
            ],
            [
                "./meeting-registry",
                "search",
                "--name",
                "Grant Budget Review",
                "--city",
                "Chicago",
            ],
        ]
    )
    accessibility_id = sole_id(
        search_results[0], "Accessibility Council Check-in in Denver"
    )
    grant_id = sole_id(search_results[1], "Grant Budget Review in Chicago")
    concurrent_action(
        [
            ["./meeting-registry", "get", "--id", accessibility_id],
            ["./meeting-registry", "get", "--id", grant_id],
        ]
    )


if __name__ == "__main__":
    main()
