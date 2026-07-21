#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "case-audit.jsonl"
DATABASE_PATH = ROOT / ".cases" / "runtime" / "cases.sqlite3"
REPORT_PATH = ROOT / "status_audit.md"
AUDIT_KEY = b"moonshiner-pi-case-audit-0105-v1"

EXPECTED_HASHES = {
    "case-registry": "56cd3365130b0ef2012acf567e27fff9a7b279f60ca59591269d24933e82b3ca",
    ".cases/seed.sql": "7bcfd793e9c0cc0ce1f53e26f29597fc647970ad4c759f049c0c09bfe89c2c0b",
    "README.md": "ac96be0ec6fbb1bc4bcfd7721ad0e88df76cfe8ae992ecab2c25fab57574a545",
    ".gitignore": "b3e8f74a7ea523b9569f576cf9f3e0d9d57f8702b46412db4b5b5041b995ca2e",
}
EXPECTED_DATABASE_DIGEST = "f3c73d20c73f54582a7868cca8644e8ebaa0d271e3a2ea0a03ced189a0410677"
EXPECTED_SEARCHES = {
    ("Damaged parcel case", "Acme Cooperative"),
    ("Duplicate membership charge", "Beacon Arts"),
}
EXPECTED_REPORT = (
    "Damaged parcel case in Acme Cooperative has status investigating.\n"
    "Duplicate membership charge in Beacon Arts has status awaiting-refund.\n"
    "The returned statuses differ.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_ROOT_ENTRIES = {
    ".cases",
    ".git",
    ".gitignore",
    ".harness",
    ".reference_solution",
    "README.md",
    "case-registry",
    "status_audit.md",
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
            fail(f"protected case-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite case registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, customer, status, opened_on, priority,
                       description, lifecycle
                FROM case_records ORDER BY stable_id
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
        fail(f"case registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("case registry database integrity check failed")
    snapshot = {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "case registry state changed; target, similarly named, related, "
            "archived, other cases, and notifications must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine case-registry execution trace was produced")
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
        for field in ("start_ns", "end_ns", "process_pid", "process_group_id"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        events.append(event)
    return sorted(events, key=lambda item: item["start_ns"])


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict, second: dict) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def separate_tool_calls(first: dict, second: dict) -> bool:
    return (
        first["process_group_id"] != second["process_group_id"]
        and first["process_pid"] == first["process_group_id"]
        and second["process_pid"] == second["process_group_id"]
        and first.get("session_id") == first["process_pid"]
        and second.get("session_id") == second["process_pid"]
    )


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("a create, update, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required case-registry operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("customer")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-customer searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both case lookups must resolve to exactly one current stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        or not event["result_ids"][0]
        for event in searches
    ):
        fail("a search did not return one auditable stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not concurrent sibling Pi Bash calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = {event["result_ids"][0] for event in searches}
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current case record was not retrieved")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the retrievals were not concurrent sibling Pi Bash calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in gets
    }:
        fail("search and retrieval stages reused a Bash call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("status_audit.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("status_audit.md is not the exact grounded three-line audit")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded case status audit and genuine Pi registry execution "
        "satisfy every dependency, concurrency, scope, and read-only constraint"
    )


if __name__ == "__main__":
    main()
