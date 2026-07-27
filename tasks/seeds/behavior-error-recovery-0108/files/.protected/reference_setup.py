#!/usr/bin/env python3
"""Exercise the reference recovery path through the genuine executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "shipment-audit.jsonl"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "3910730b30b570f0041c80edb55c5ae9c1499dd4d586fc5aa2fec37cea26a891"

ROUTER_COMMAND = [
    "./shipment-registry",
    "availability",
    "--item",
    "Replacement routers",
    "--city",
    "Omaha",
    "--date",
    "2026-10-21",
]
ART_COMMAND = [
    "./shipment-registry",
    "availability",
    "--item",
    "School art supplies",
    "--city",
    "Albuquerque",
    "--date",
    "2026-10-21",
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

    router_process = run_process(ROUTER_COMMAND)
    art_process = run_process(ART_COMMAND)
    router_stdout, router_stderr = router_process.communicate(timeout=5)
    art_stdout, art_stderr = art_process.communicate(timeout=5)

    if router_process.returncode != 0 or router_stderr:
        raise RuntimeError("reference router branch did not succeed cleanly")
    router = decode_json(router_stdout, "router branch")
    if router.get("available") is not True:
        raise RuntimeError("reference router availability was unexpected")

    if art_process.returncode != 75 or art_stdout:
        raise RuntimeError("reference art branch did not return a transient error")
    art_error = decode_json(art_stderr, "art transient error")
    if (
        art_error.get("kind") != "transient"
        or art_error.get("retryable") is not True
    ):
        raise RuntimeError("reference art failure was not explicitly retryable")

    retry_process = run_process(ART_COMMAND)
    retry_stdout, retry_stderr = retry_process.communicate(timeout=5)
    if retry_process.returncode != 0 or retry_stderr:
        raise RuntimeError("reference art retry did not succeed cleanly")
    retry = decode_json(retry_stdout, "art retry")
    if retry.get("available") is not False:
        raise RuntimeError("reference art availability was unexpected")


if __name__ == "__main__":
    main()
