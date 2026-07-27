#!/usr/bin/env python3
"""Exercise the reference answer through the genuine campaign executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_RUNTIME = ROOT / ".campaigns" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
REPORT_PATH = ROOT / "handoff.md"
REPORT_DIGEST = "6c9e8b5faf164021ef717d016364fc4c7a0f0736c9d3feaac22ffb919018db9f"


def reset_generated_state() -> None:
    for path in (
        CAMPAIGN_RUNTIME / "campaigns.sqlite3",
        CAMPAIGN_RUNTIME / "campaigns.sqlite3-shm",
        CAMPAIGN_RUNTIME / "campaigns.sqlite3-wal",
        CAMPAIGN_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "campaign-audit.jsonl",
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
                f"reference campaign operation failed: {stderr.decode().strip()}"
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


def complete_record(payload: dict, stable_id: str, label: str) -> dict:
    record = payload.get("record")
    if not isinstance(record, dict) or record.get("stable_id") != stable_id:
        raise RuntimeError(f"reference retrieval was not complete: {label}")
    for field in ("title", "collection", "status", "date"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise RuntimeError(f"reference retrieval lacks {field}: {label}")
    return record


def main() -> None:
    if (
        not REPORT_PATH.is_file()
        or hashlib.sha256(REPORT_PATH.read_bytes()).hexdigest() != REPORT_DIGEST
    ):
        return

    reset_generated_state()
    searches = concurrent_action(
        [
            [
                "./campaign-registry",
                "search",
                "--title",
                "Volunteer renewal reminder",
                "--collection",
                "Volunteers",
            ],
            [
                "./campaign-registry",
                "search",
                "--title",
                "North region service bulletin",
                "--collection",
                "North Region",
            ],
        ]
    )
    volunteer_id = sole_id(
        searches[0], "Volunteer renewal reminder in Volunteers"
    )
    north_id = sole_id(
        searches[1], "North region service bulletin in North Region"
    )
    retrieved = concurrent_action(
        [
            ["./campaign-registry", "get", "--id", volunteer_id],
            ["./campaign-registry", "get", "--id", north_id],
        ]
    )
    complete_record(
        retrieved[0], volunteer_id, "Volunteer renewal reminder in Volunteers"
    )
    complete_record(
        retrieved[1], north_id, "North region service bulletin in North Region"
    )


if __name__ == "__main__":
    main()
