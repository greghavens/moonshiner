#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine facilities executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".facilities" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
REPORT_PATH = ROOT / "facilities_check.md"
REFERENCE_REPORT_DIGEST = (
    "2c03bc3cbc97f432fd6df96ab75b5caf1cb28557ddf6df327e5e82f1d20058b2"
)


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "facilities.sqlite3",
        REGISTRY_RUNTIME / "facilities.sqlite3-shm",
        REGISTRY_RUNTIME / "facilities.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "facilities-audit.jsonl",
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
    request_id = matches[0].get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise RuntimeError(f"reference lookup returned no stable request ID: {label}")
    return request_id


def main() -> None:
    if (
        not REPORT_PATH.is_file()
        or hashlib.sha256(REPORT_PATH.read_bytes()).hexdigest()
        != REFERENCE_REPORT_DIGEST
    ):
        return

    report = REPORT_PATH.read_bytes()
    REPORT_PATH.unlink()
    try:
        reset_generated_state()
        search_results = concurrent_action(
            [
                [
                    "./facilities-registry",
                    "search",
                    "--name",
                    "Clinic air filter replacement",
                    "--location",
                    "Health Center",
                ],
                [
                    "./facilities-registry",
                    "search",
                    "--name",
                    "Museum gallery repainting",
                    "--location",
                    "Arts Center",
                ],
            ]
        )
        clinic_id = sole_id(
            search_results[0], "Clinic air filter replacement at Health Center"
        )
        museum_id = sole_id(
            search_results[1], "Museum gallery repainting at Arts Center"
        )
        concurrent_action(
            [
                ["./facilities-registry", "get", "--id", clinic_id],
                ["./facilities-registry", "get", "--id", museum_id],
            ]
        )
    finally:
        REPORT_PATH.write_bytes(report)


if __name__ == "__main__":
    main()
