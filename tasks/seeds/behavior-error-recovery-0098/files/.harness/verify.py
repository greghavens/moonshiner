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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "subscription-audit.jsonl"
DATABASE_PATH = ROOT / ".subscription" / "runtime" / "subscriptions.sqlite3"
REPORT_PATH = ROOT / "availability_audit.md"
AUDIT_KEY = b"moonshiner-pi-subscription-error-recovery-0098-v1"

EXPECTED_HASHES = {
    "subscriptionctl": "0f502a6919ee42b3a98a6d281d6ee2a7175fde5be25eb6f1df09d9b526cdf3e6",
    ".subscription/seed.sql": "08ff006fa0a929d3cc1ada46cb22dc996c819a161b3a28f11783e31c928659fc",
    "README.md": "2a273a8c10d14f7517f3550cf6ba7bb73248a8b94af0e09d4816b193a944cace",
    ".gitignore": "9059de03a39c5d2374711926382c38b761a7a8f5838862d81055da42daad6c32",
}
EXPECTED_DATABASE_DIGEST = "7ce678c214ac0312c67ae09925e0ff46706564a901d42123787c20232104bc2e"
EXPECTED_TARGETS = {
    ("Clinic backup line", "Health Account", "2026-09-06"),
    ("Museum guest Wi-Fi plan", "Arts Account", "2026-09-06"),
}
EXPECTED_REPORT = (
    "Clinic backup line in Health Account on 2026-09-06: available.\n"
    "Museum guest Wi-Fi plan in Arts Account on 2026-09-06: unavailable.\n"
    "The transiently failed check was retried once; the successful initial check was not repeated.\n"
)
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".reference_solution",
    ".subscription",
    "README.md",
    "availability_audit.md",
    "subscriptionctl",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected subscription environment changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite subscription registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            subscriptions = connection.execute(
                """
                SELECT stable_id, name, account, lifecycle
                  FROM subscriptions ORDER BY stable_id
                """
            ).fetchall()
            availability = connection.execute(
                """
                SELECT stable_id, service_date, availability
                  FROM availability ORDER BY stable_id, service_date
                """
            ).fetchall()
            rules = connection.execute(
                """
                SELECT stable_id, service_date, failed_attempts
                  FROM transient_rules ORDER BY stable_id, service_date
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
        fail(f"subscription registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("subscription registry database integrity check failed")
    snapshot = {
        "subscriptions": [list(row) for row in subscriptions],
        "availability": [list(row) for row in availability],
        "transient_rules": [list(row) for row in rules],
        "notifications": [list(row) for row in notifications],
    }
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail("subscription registry state changed during the read-only check")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine subscriptionctl execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by subscriptionctl")
        for field in ("start_ns", "end_ns", "process_pid", "process_group_id"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        events.append(event)
    return sorted(events, key=lambda item: item["start_ns"])


def target(event: dict) -> tuple[object, object, object]:
    return (event.get("name"), event.get("account"), event.get("date"))


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(events: list[dict]) -> bool:
    parents = {
        (event.get("parent_pid"), event.get("parent_start_ticks"))
        for event in events
    }
    return len(parents) == 1 and next(iter(parents))[1] != "unavailable"


def separate_tool_call(event: dict) -> bool:
    return (
        event["process_pid"] == event["process_group_id"]
        and event.get("session_id") == event["process_pid"]
    )


def verify_execution(events: list[dict]) -> None:
    if len(events) != 3:
        fail("expected exactly two initial checks and one failed-branch retry")
    if any(event.get("operation") != "availability" for event in events):
        fail("a create, cancel, update, notify, or unknown operation was executed")

    initial = events[:2]
    retry = events[2]
    if {target(event) for event in initial} != EXPECTED_TARGETS:
        fail("the initial action did not contain exactly both requested checks")
    if any(event.get("attempt") != 1 for event in initial):
        fail("an initial branch had already been attempted or was repeated")
    if not overlaps(initial[0], initial[1]):
        fail("the two initial availability checks did not execute concurrently")
    if not same_harness_parent(events) or not all(
        separate_tool_call(event) for event in events
    ):
        fail("the checks were not issued as direct Pi Bash calls")

    successes = [event for event in initial if event.get("success") is True]
    transient_failures = [
        event
        for event in initial
        if event.get("success") is False
        and event.get("error_kind") == "transient"
        and event.get("retryable") is True
    ]
    if len(successes) != 1 or len(transient_failures) != 1:
        fail("the initial parallel action did not preserve one success after one transient failure")
    success = successes[0]
    failed = transient_failures[0]

    if retry["start_ns"] <= max(event["end_ns"] for event in initial):
        fail("the failed branch was retried before both initial calls finished")
    if target(retry) != target(failed):
        fail("the retry did not repeat only the transiently failed exact check")
    if target(retry) == target(success):
        fail("the successful initial branch was repeated")
    if retry.get("attempt") != 2:
        fail("the failed branch was not retried exactly once")
    if retry.get("success") is not True or retry.get("result_count") != 1:
        fail("the single permitted retry did not return availability")
    if retry.get("process_group_id") in {
        event.get("process_group_id") for event in initial
    }:
        fail("the retry was not a new Pi Bash action")

    successful_results = {target(success): success.get("availability"), target(retry): retry.get("availability")}
    if successful_results != {
        ("Clinic backup line", "Health Account", "2026-09-06"): "available",
        ("Museum guest Wi-Fi plan", "Arts Account", "2026-09-06"): "unavailable",
    }:
        fail("the successful tool results do not ground the requested availability report")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("availability_audit.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("availability_audit.md is not the exact grounded three-line report")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: genuine parallel checks preserved the successful branch and "
        "retried only the transiently failed branch once"
    )


if __name__ == "__main__":
    main()
