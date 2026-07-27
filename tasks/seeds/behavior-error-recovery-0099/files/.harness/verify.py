#!/usr/bin/env python3
"""Deterministic protected verifier for Pi uncertain-commit recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
AUDIT_PATH = RUNTIME / "rebate-audit.jsonl"
DB_PATH = RUNTIME / "rebates.sqlite3"
RESULT_PATH = ROOT / "cancellation_result.txt"
AUDIT_KEY = b"moonshiner-pi-rebate-audit-0099-v1"
TARGET_ID = "pub-199"
DISTRACTOR_ID = "pub-919"
REASON = "no longer needed for the scheduled work."

EXPECTED_HASHES = {
    "rebate-desk": "915ae52bc4205e566d428f279c04a73c03b2cd7ee1d4f7ee2d595aaa221da4e5",
    ".rebates/seed.sql": "cafc8ce3996efb6fefa48b39898f2252ea25fdc01737106a36b25919a3dc92e6",
    "README.md": "8a07a9a59b7c577999678cecc564c8b397ed1887420bf0e50f00174a7e51f85c",
    ".gitignore": "7a98ed3a35ccd25f34482b0d3b60d6c9db881b3e2fece29806808d733ba04f0f",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            fail(f"protected public-services input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine rebate-desk execution evidence was produced")
    events: list[dict] = []
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {number} was not emitted intact by rebate-desk")
        events.append(event)
    return events


def verify_execution(events: list[dict]) -> None:
    if len(events) != 2:
        fail("expected one cancellation attempt followed by one exact-ID retrieval")
    cancellation, retrieval = events
    if cancellation.get("operation") != "cancel" or retrieval.get("operation") != "get":
        fail("the operation sequence was not cancel followed by exact-ID get")
    if cancellation.get("stable_id") != TARGET_ID:
        fail("the cancellation did not use the required stable ID")
    if cancellation.get("reason") != REASON:
        fail("the cancellation reason was not preserved exactly")
    if (
        cancellation.get("status_before") != "active"
        or cancellation.get("status_after") != "cancelled"
        or cancellation.get("mutation_committed") is not True
        or cancellation.get("client_outcome") != "timeout"
        or cancellation.get("success") is not False
        or cancellation.get("error") != "request deadline exceeded after dispatch"
    ):
        fail("the initial cancellation did not record the protected uncertain commit")
    if retrieval.get("stable_id") != TARGET_ID:
        fail("uncertain state was not resolved by retrieving the exact stable ID")
    if retrieval.get("returned_status") != "cancelled" or retrieval.get("success") is not True:
        fail("the exact-ID retrieval did not verify the committed cancellation")
    if retrieval.get("started_ns", 0) <= cancellation.get("finished_ns", 0):
        fail("the exact-ID retrieval began before the timed-out cancellation finished")
    if any(event.get("operation") in {"search", "list", "update"} for event in events):
        fail("an out-of-scope registry operation was executed")


def verify_database() -> None:
    if not DB_PATH.is_file():
        fail("the genuine runtime SQLite database is missing")
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        target = connection.execute(
            "SELECT name, status, cancel_reason FROM applications WHERE stable_id = ?",
            (TARGET_ID,),
        ).fetchone()
        distractor = connection.execute(
            "SELECT name, status, cancel_reason FROM applications WHERE stable_id = ?",
            (DISTRACTOR_ID,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT stable_id, reason, status_before, status_after "
            "FROM cancellation_requests ORDER BY request_number"
        ).fetchall()
    finally:
        connection.close()

    if target != ("Garden water rebate — Elm Street", "cancelled", REASON):
        fail("the target application is not cancelled with the exact reason")
    if distractor != (
        "Garden water rebate — Elm Street Extension",
        "active",
        None,
    ):
        fail("the similarly named application was touched")
    if attempts != [(TARGET_ID, REASON, "active", "cancelled")]:
        fail("the service did not receive exactly one target cancellation request")


def verify_result() -> None:
    if not RESULT_PATH.is_file():
        fail("cancellation_result.txt is missing")
    if RESULT_PATH.read_text(encoding="utf-8") != "pub-199 | cancelled\n":
        fail("cancellation_result.txt does not contain the exact verified final status")


def main() -> None:
    verify_protected_inputs()
    events = load_events()
    verify_execution(events)
    verify_database()
    verify_result()
    print(
        "PASS: genuine Pi cancellation, exact-ID recovery, conditional retry discipline, "
        "and distractor integrity verified"
    )


if __name__ == "__main__":
    main()
