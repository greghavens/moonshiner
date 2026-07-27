#!/usr/bin/env python3
"""Exercise the reference path through the genuine catalog executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CATALOG_RUNTIME = ROOT / ".library" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
REPORT = ROOT / "circulation_outcome.md"
REPORT_DIGEST = "9d4a73940035a489065defe4aa1b43b7d8095b8a603a52af32252ad68dc86aa4"


def reset_generated_state() -> None:
    for path in (
        CATALOG_RUNTIME / "library.sqlite3",
        CATALOG_RUNTIME / "library.sqlite3-shm",
        CATALOG_RUNTIME / "library.sqlite3-wal",
        CATALOG_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "library-audit.jsonl",
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
                f"reference catalog operation failed: {stderr.decode().strip()}"
            )
        results.append(json.loads(stdout))
    return results


def one_action(command: list[str]) -> dict:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        check=False,
        start_new_session=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"reference catalog operation failed: {completed.stderr.decode().strip()}"
        )
    return json.loads(completed.stdout)


def sole_id(payload: dict, label: str) -> str:
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference lookup did not resolve uniquely: {label}")
    stable_id = matches[0].get("stable_id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError(f"reference lookup returned no stable ID: {label}")
    return stable_id


def record(payload: dict, label: str) -> dict:
    value = payload.get("record")
    if not isinstance(value, dict):
        raise RuntimeError(f"reference operation returned no record: {label}")
    return value


def main() -> None:
    if (
        not REPORT.is_file()
        or hashlib.sha256(REPORT.read_bytes()).hexdigest() != REPORT_DIGEST
    ):
        return

    reset_generated_state()
    search_results = concurrent_action(
        [
            [
                "./library-catalog",
                "search",
                "--name",
                "The Quiet Observatory",
                "--branch",
                "Central Branch",
            ],
            [
                "./library-catalog",
                "search",
                "--name",
                "Cooking with Winter Roots",
                "--branch",
                "East Branch",
            ],
        ]
    )
    quiet_id = sole_id(
        search_results[0], "The Quiet Observatory at Central Branch"
    )
    cooking_id = sole_id(
        search_results[1], "Cooking with Winter Roots at East Branch"
    )
    get_results = concurrent_action(
        [
            ["./library-catalog", "get", "--id", quiet_id],
            ["./library-catalog", "get", "--id", cooking_id],
        ]
    )
    quiet = record(get_results[0], "The Quiet Observatory")
    cooking = record(get_results[1], "Cooking with Winter Roots")
    if quiet.get("name") != "The Quiet Observatory":
        raise RuntimeError("reference retrieval returned the wrong quiet title")
    if cooking.get("status") != "on-loan":
        raise RuntimeError("reference conditional status is not on-loan")

    updated = record(
        one_action(
            [
                "./library-catalog",
                "update",
                "--id",
                cooking_id,
                "--status",
                "on-hold",
            ]
        ),
        "Cooking with Winter Roots update",
    )
    if updated.get("stable_id") != cooking_id or updated.get("status") != "on-hold":
        raise RuntimeError("reference conditional update did not succeed")

    notice = one_action(
        [
            "./library-catalog",
            "notify",
            "--id",
            cooking_id,
            "--recipient",
            "circulation desk",
        ]
    ).get("notification")
    if not isinstance(notice, dict) or notice.get("recipient") != "circulation desk":
        raise RuntimeError("reference dependent notice did not succeed")


if __name__ == "__main__":
    main()
