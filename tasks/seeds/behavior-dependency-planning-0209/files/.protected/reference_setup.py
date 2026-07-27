#!/usr/bin/env python3
"""Exercise the reference answer through the genuine account executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_RUNTIME = ROOT / ".accounts" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
REPORT_PATH = ROOT / "account-check.md"
REPORT_DIGEST = "c1506ada34a23336036311c5c00d09dad329c38fe22d54d5713f98e1882b34ec"


def reset_generated_state() -> None:
    for path in (
        ACCOUNT_RUNTIME / "accounts.sqlite3",
        ACCOUNT_RUNTIME / "accounts.sqlite3-shm",
        ACCOUNT_RUNTIME / "accounts.sqlite3-wal",
        ACCOUNT_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "account-audit.jsonl",
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
                "./account-registry",
                "search",
                "--name",
                "Arbor Foods renewal",
                "--region",
                "West Region",
            ],
            [
                "./account-registry",
                "search",
                "--name",
                "Bright Dental onboarding",
                "--region",
                "Central Region",
            ],
        ]
    )
    arbor_id = sole_id(search_results[0], "Arbor Foods renewal in West Region")
    bright_id = sole_id(
        search_results[1], "Bright Dental onboarding in Central Region"
    )
    concurrent_action(
        [
            ["./account-registry", "get", "--id", arbor_id],
            ["./account-registry", "get", "--id", bright_id],
        ]
    )


if __name__ == "__main__":
    main()
