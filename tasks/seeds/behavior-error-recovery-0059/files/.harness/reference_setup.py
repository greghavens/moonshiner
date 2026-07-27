#!/usr/bin/env python3
"""Exercise the reference solution through the genuine registry executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".public_services" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
RECEIPT = ROOT / "cancellation_receipt.md"
RECEIPT_DIGEST = "fed670a8abc68bf60a34e94f3612cbf1bd951b85df94f09bbb7787ac798e0204"
TARGET_ID = "pub-159"
REASON = "created for the wrong operating period"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "public-services.sqlite3",
        REGISTRY_RUNTIME / "public-services.sqlite3-shm",
        REGISTRY_RUNTIME / "public-services.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "public-services-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_registry(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["./public-services", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5,
    )


def returned_status(result: subprocess.CompletedProcess[str], label: str) -> str:
    if result.returncode != 0:
        raise RuntimeError(f"reference {label} failed: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
        status = payload["record"]["status"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"reference {label} returned no record status") from exc
    if not isinstance(status, str):
        raise RuntimeError(f"reference {label} returned an invalid record status")
    return status


def main() -> None:
    if (
        not RECEIPT.is_file()
        or hashlib.sha256(RECEIPT.read_bytes()).hexdigest() != RECEIPT_DIGEST
    ):
        return

    reset_generated_state()
    first = run_registry("cancel", "--id", TARGET_ID, "--reason", REASON)
    if first.returncode != 75 or "commit status unknown" not in first.stderr:
        raise RuntimeError("reference first cancellation did not time out as uncertain")

    retrieved = run_registry("get", "--id", TARGET_ID)
    if returned_status(retrieved, "direct retrieval") != "active":
        raise RuntimeError("reference direct retrieval did not find the active branch")

    retry = run_registry("cancel", "--id", TARGET_ID, "--reason", REASON)
    if returned_status(retry, "identical retry") != "cancelled":
        raise RuntimeError("reference retry did not cancel the target")


if __name__ == "__main__":
    main()
