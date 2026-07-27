#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi error-recovery seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "public-services-audit.jsonl"
DATABASE_PATH = ROOT / ".public_services" / "runtime" / "public-services.sqlite3"
RECEIPT_PATH = ROOT / "cancellation_receipt.md"
AUDIT_KEY = b"moonshiner-pi-public-services-error-recovery-0059-v1"
TARGET_ID = "pub-159"
REASON = "created for the wrong operating period"

EXPECTED_HASHES = {
    "public-services": "a3e525ab270224890f3413a4747fc815845b3f55dc12d6d87f60448566cbdbb4",
    ".public_services/seed.sql": "15d82687c2d1d64ee7b83e837c61eb87d158c13fab1f180dc429293981648127",
    "README.md": "ff1d1ca1fe1d9909a1c7f22a1b7954ac287bc05569fb734f54f005d3211685da",
    ".gitignore": "0b2fcd7ac3149e7e4b16517b9b9db2e1f5fcb2e71ea06c6499649d133761e7f7",
}
EXPECTED_RECEIPT = (
    "Target: pub-159\n"
    "Post-timeout verification: active\n"
    "Retry: sent\n"
    "Final cancellation status: cancelled\n"
    "Reason: created for the wrong operating period\n"
)
EXPECTED_RECORDS = [
    ("pub-159", "Pet license", "Aurora", "cancelled", REASON),
    ("pub-559", "Facility permit", "Lakewood", "pending", None),
    ("pub-659", "Pet license", "Lakewood", "closed", None),
]
EXPECTED_REQUESTS = [
    (1, TARGET_ID, REASON, "timed_out_before_commit"),
    (2, TARGET_ID, REASON, "committed"),
]
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".public_services",
    "README.md",
    "cancellation_receipt.md",
    "public-services",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected public-services input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))

    allowed_public_services = {
        ".public_services/seed.sql",
        ".public_services/runtime",
        ".public_services/runtime/initialize.lock",
        ".public_services/runtime/public-services.sqlite3",
    }
    observed_public_services = {
        str(path.relative_to(ROOT))
        for path in (ROOT / ".public_services").rglob("*")
    }
    unexpected_public_services = sorted(
        observed_public_services - allowed_public_services
    )
    if unexpected_public_services:
        fail(
            "unexpected artifact in protected registry storage: "
            + ", ".join(unexpected_public_services)
        )

    allowed_harness = {
        ".harness/reference_setup.py",
        ".harness/verify.py",
        ".harness/runtime",
        ".harness/runtime/public-services-audit.jsonl",
    }
    observed_harness = {
        str(path.relative_to(ROOT)) for path in (ROOT / ".harness").rglob("*")
    }
    unexpected_harness = sorted(observed_harness - allowed_harness)
    if unexpected_harness:
        fail(
            "unexpected artifact in protected harness storage: "
            + ", ".join(unexpected_harness)
        )


def verify_receipt() -> None:
    if not RECEIPT_PATH.is_file():
        fail("cancellation_receipt.md was not created")
    try:
        receipt = RECEIPT_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail("cancellation_receipt.md is not UTF-8 text")
    if receipt != EXPECTED_RECEIPT:
        fail("cancellation_receipt.md does not reflect the verified retry outcome")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite public-services registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, location, status, cancellation_reason
                FROM records ORDER BY stable_id
                """
            ).fetchall()
            requests = connection.execute(
                """
                SELECT request_id, stable_id, reason, outcome
                FROM cancellation_requests ORDER BY request_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"public-services registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("public-services registry database integrity check failed")
    if records != EXPECTED_RECORDS:
        fail("target or non-target public-services record state is incorrect")
    if requests != EXPECTED_REQUESTS:
        fail("cancellation request history is not one timeout plus one identical retry")
    if notifications:
        fail("a notification or contact attempt was recorded")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine public-services execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"audit event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by the registry")
        for field in ("start_ns", "end_ns", "process_pid"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        events.append(event)
    return events


def verify_execution(events: list[dict]) -> None:
    if len(events) != 3:
        fail("expected exactly a cancellation, direct retrieval, and one retry")
    if [event.get("operation") for event in events] != ["cancel", "get", "cancel"]:
        fail("registry operations were not cancellation, retrieval, then retry")

    first, retrieved, retry = events
    if not (
        first.get("success") is False
        and first.get("error")
        == "gateway timeout after request reached registry; commit status unknown"
        and first.get("stable_id") == TARGET_ID
        and first.get("reason") == REASON
        and first.get("service_attempt") == 1
        and first.get("service_reached") is True
        and first.get("may_have_committed") is True
    ):
        fail("the first cancellation was not the required uncertain timeout")

    if not (
        retrieved.get("success") is True
        and retrieved.get("error") is None
        and retrieved.get("stable_id") == TARGET_ID
        and retrieved.get("returned_status") == "active"
    ):
        fail("the timeout was not resolved by one direct active-status retrieval")

    if not (
        retry.get("success") is True
        and retry.get("error") is None
        and retry.get("stable_id") == TARGET_ID
        and retry.get("reason") == REASON
        and retry.get("service_attempt") == 2
        and retry.get("returned_status") == "cancelled"
    ):
        fail("the retry was not one identical successful cancellation")

    if not (
        first["end_ns"] <= retrieved["start_ns"]
        and retrieved["end_ns"] <= retry["start_ns"]
    ):
        fail("a retry began before the direct verification result was available")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_receipt()
    verify_database_state()
    verify_execution(load_events())
    print("PASS: uncertain cancellation was verified and retried exactly once")


if __name__ == "__main__":
    main()
