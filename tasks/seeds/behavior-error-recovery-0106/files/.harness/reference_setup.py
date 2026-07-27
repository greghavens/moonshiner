#!/usr/bin/env python3
"""Exercise the reference recovery path through the genuine order executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "order-audit.jsonl"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "b5495d4b30b3deb714c93259c0a117fd03ff8b9cf2343d7f05090806f9725a71"

ARCHIVE_COMMAND = [
    "./order-registry",
    "availability",
    "--name",
    "Archive storage boxes",
    "--location",
    "Richmond",
    "--date",
    "2026-10-20",
]
COAT_COMMAND = [
    "./order-registry",
    "availability",
    "--name",
    "Winter coat donation order",
    "--location",
    "Detroit",
    "--date",
    "2026-10-20",
]


def run_process(command: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def decode_json(raw: bytes, label: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"reference {label} did not return JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"reference {label} returned a non-object")
    return payload


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    AUDIT_PATH.unlink(missing_ok=True)

    archive_process = run_process(ARCHIVE_COMMAND)
    coat_process = run_process(COAT_COMMAND)
    archive_stdout, archive_stderr = archive_process.communicate()
    coat_stdout, coat_stderr = coat_process.communicate()

    if archive_process.returncode != 0 or archive_stderr:
        raise RuntimeError("reference successful branch did not succeed cleanly")
    archive = decode_json(archive_stdout, "archive branch")
    if archive.get("availability") != "available":
        raise RuntimeError("reference archive availability was unexpected")

    if coat_process.returncode != 75 or coat_stdout:
        raise RuntimeError("reference failed branch did not produce a transient error")
    coat_error = decode_json(coat_stderr, "coat transient error")
    if coat_error.get("kind") != "transient" or coat_error.get("retryable") is not True:
        raise RuntimeError("reference failed branch was not explicitly retryable")

    retry_process = run_process(COAT_COMMAND)
    retry_stdout, retry_stderr = retry_process.communicate()
    if retry_process.returncode != 0 or retry_stderr:
        raise RuntimeError("reference retry did not succeed cleanly")
    retry = decode_json(retry_stdout, "coat retry")
    if retry.get("availability") != "unavailable":
        raise RuntimeError("reference coat availability was unexpected")


if __name__ == "__main__":
    main()
