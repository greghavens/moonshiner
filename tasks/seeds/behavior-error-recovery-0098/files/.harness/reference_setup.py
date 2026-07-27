#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine subscription executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".subscription" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "45ffd061cc55260771b6795b41cf7ea8e385edb7a15f9f02dc3ed4b2d715bcb4"
TARGETS = (
    ("Clinic backup line", "Health Account", "2026-09-06"),
    ("Museum guest Wi-Fi plan", "Arts Account", "2026-09-06"),
)


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "subscriptions.sqlite3",
        REGISTRY_RUNTIME / "subscriptions.sqlite3-shm",
        REGISTRY_RUNTIME / "subscriptions.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "attempts.json",
        HARNESS_RUNTIME / "attempts.lock",
        HARNESS_RUNTIME / "subscription-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def start_call(target: tuple[str, str, str]) -> subprocess.Popen[bytes]:
    name, account, service_date = target
    return subprocess.Popen(
        [
            "./subscriptionctl",
            "availability",
            "--name",
            name,
            "--account",
            account,
            "--date",
            service_date,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def finish_call(process: subprocess.Popen[bytes]) -> tuple[int, dict]:
    stdout, stderr = process.communicate(timeout=10)
    payload = json.loads(stdout if process.returncode == 0 else stderr)
    return process.returncode, payload


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    initial = [start_call(target) for target in TARGETS]
    outcomes = [finish_call(process) for process in initial]
    failures = [
        index
        for index, (returncode, payload) in enumerate(outcomes)
        if returncode == 75
        and payload.get("error_kind") == "transient"
        and payload.get("retryable") is True
    ]
    successes = [index for index, (returncode, _) in enumerate(outcomes) if returncode == 0]
    if len(failures) != 1 or len(successes) != 1:
        raise RuntimeError("reference initial checks did not partially fail as required")

    retry = start_call(TARGETS[failures[0]])
    retry_code, retry_payload = finish_call(retry)
    if retry_code != 0 or not isinstance(retry_payload.get("result"), dict):
        raise RuntimeError("reference failed-branch retry did not succeed")


if __name__ == "__main__":
    main()
