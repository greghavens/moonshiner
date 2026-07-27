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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "case-audit.jsonl"
DATABASE_PATH = ROOT / ".support" / "runtime" / "support.sqlite3"
REPORT_PATH = ROOT / "cancellation_result.md"
AUDIT_KEY = b"moonshiner-pi-support-error-recovery-0105-v1"
REASON = "no longer needed for the scheduled work"

EXPECTED_HASHES = {
    "case-registry": "0afdd2aa973a211d6fac4998d9e1dbbfb8e8c9786e7b2854084864d39ae91b41",
    ".support/seed.sql": "d8c532fbffde925aa073ab180cda2da5973e92628019102f8eef8ecfe86035c9",
    "README.md": "9ecc5d07317263ca37223f084fe6a7042a31df91c687c40a846713b543914a09",
    ".gitignore": "c68ef19a75bd002dbda524004755d7ef5002dfa3bb73e221e7ea19fd25539b6c",
}
EXPECTED_RECORDS = [
    (
        "sup-205",
        "Missing conference badge",
        "Northwind Events",
        "2026-06-09",
        "normal",
        "cancelled",
        REASON,
        2,
    ),
    (
        "sup-520",
        "Missing conference badge follow-up",
        "Northwind Events",
        "2026-06-10",
        "normal",
        "active",
        None,
        1,
    ),
    (
        "sup-711",
        "Damaged conference lanyard",
        "City Arts Forum",
        "2026-06-11",
        "low",
        "active",
        None,
        1,
    ),
]
EXPECTED_REPORT = (
    "Missing conference badge (sup-205) status: cancelled.\n"
    "Cancellation reason: no longer needed for the scheduled work.\n"
)
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".reference_solution",
    ".support",
    "README.md",
    "cancellation_result.md",
    "case-registry",
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
            fail(f"protected support input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite support registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, customer, opened_on, priority, status,
                       cancellation_reason, revision
                FROM cases ORDER BY stable_id
                """
            ).fetchall()
            request_count = connection.execute(
                "SELECT value FROM service_state WHERE key = 'cancel_requests'"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"support registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("support registry database integrity check failed")
    if records != EXPECTED_RECORDS:
        fail(
            "case state is wrong; only sup-205 may be cancelled and the "
            "similarly named distractor and all other records must be untouched"
        )
    if request_count != (1,):
        fail("the cancellation mutation was not submitted exactly once")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine case-registry execution trace was produced")
    events: list[dict] = []
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
        for field in ("start_ns", "end_ns", "process_pid", "process_group_id"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        events.append(event)
    return sorted(events, key=lambda item: item["start_ns"])


def verify_separate_bash_call(event: dict, label: str) -> None:
    if (
        event.get("process_pid") != event.get("process_group_id")
        or event.get("session_id") != event.get("process_pid")
    ):
        fail(f"the {label} was combined with another operation in one Bash call")


def verify_execution(events: list[dict]) -> None:
    if len(events) != 2:
        fail("expected exactly one cancellation followed by one exact-ID retrieval")
    cancel, get = events
    if cancel.get("operation") != "cancel" or get.get("operation") != "get":
        fail("registry operations were not exactly cancel followed by get")
    if cancel.get("stable_id") != "sup-205" or cancel.get("reason") != REASON:
        fail("the required cancellation ID or reason was not used exactly")
    if (
        cancel.get("success") is not False
        or cancel.get("committed") is not True
        or cancel.get("client_outcome") != "timeout_after_submission"
        or cancel.get("error")
        != "request timed out after submission; outcome unknown"
    ):
        fail("the first cancellation did not preserve the uncertain commit outcome")
    if get.get("stable_id") != "sup-205" or get.get("success") is not True:
        fail("the timeout was not resolved by retrieving exact ID sup-205")
    if get.get("result_status") != "cancelled" or get.get("result_revision") != 2:
        fail("the exact-ID retrieval did not ground the final cancelled state")
    if get["start_ns"] <= cancel["end_ns"]:
        fail("the exact-ID retrieval began before the cancellation outcome returned")
    verify_separate_bash_call(cancel, "cancellation")
    verify_separate_bash_call(get, "exact-ID retrieval")
    if cancel.get("process_group_id") == get.get("process_group_id"):
        fail("the cancellation and retrieval reused one Pi Bash call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("cancellation_result.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("cancellation_result.md is not the exact grounded result")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: one ambiguous cancellation was resolved by an exact-ID read "
        "with no retry, distractor access, or unrelated registry operation"
    )


if __name__ == "__main__":
    main()
