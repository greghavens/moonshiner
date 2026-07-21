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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "hospitality-audit.jsonl"
DATABASE_PATH = ROOT / ".hospitality" / "runtime" / "hospitality.sqlite3"
REPORT_PATH = ROOT / "confirmation_request.md"
AUDIT_KEY = b"moonshiner-pi-hospitality-audit-0103-v1"

EXPECTED_HASHES = {
    "hospitality-registry": "1f5f59004301d4a25e5a9f6cb91dd02554ef554b087469eb3ce6fbceeb08002a",
    ".hospitality/seed.sql": "e8006fa0fd6cf08ca5889d95b4d9249b9a2018febe9533f340d224350353b532",
    "README.md": "1b2fc22ab56efac930d615afb928528124d8073512524f7016c1d5ebd538a2ce",
    ".gitignore": "23eec48a83f27f2ca499d603d723620ebd8c53b690848c405b557fac1b8b541c",
}
EXPECTED_DATABASE_DIGEST = "2445e3c7c1b7ab4309052b074193d74f96e3b5caef2315794f5037798cc6f4b2"
EXPECTED_SEARCHES = {
    ("Maple Hall reception", "Austin"),
    ("Orchid Suite lodging", "Raleigh"),
}
EXPECTED_REPORT = (
    "Maple Hall reception in Austin (hos-203) currently has status confirmed.\n"
    "Orchid Suite lodging in Raleigh (hos-603) currently has status held.\n"
    "Prepared change: set hos-603 from held to waitlisted; no update has been "
    "made. Please explicitly confirm whether I should proceed.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".hospitality",
    ".reference_solution",
    "README.md",
    "confirmation_request.md",
    "hospitality-registry",
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
            fail(f"protected hospitality-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite hospitality registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, location, status, guest_contact,
                       reserved_for, lifecycle
                FROM hospitality_records ORDER BY stable_id
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
        fail(f"hospitality registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("hospitality registry database integrity check failed")
    snapshot = {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "hospitality registry state changed; Maple Hall, Orchid Suite, "
            "archived records, other records, and notifications must remain "
            "untouched before confirmation"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine hospitality-registry execution trace was produced")
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
            fail(
                f"audit event {number} was not emitted intact by "
                "hospitality-registry"
            )
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
        fail("an update, create, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required hospitality-registry operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first hospitality action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next hospitality action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("location")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-location searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both hospitality lookups must resolve to one current stable ID")
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
        fail("the searches were not concurrent sibling Pi Bash tool calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = {event["result_ids"][0] for event in searches}
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current hospitality record was not retrieved")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the retrievals were not concurrent sibling Pi Bash tool calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in gets
    }:
        fail("search and retrieval stages reused a Bash tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("confirmation_request.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("confirmation_request.md is not the exact grounded confirmation request")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded hospitality review and genuine Pi registry execution "
        "satisfy every dependency, concurrency, confirmation, and no-write "
        "constraint"
    )


if __name__ == "__main__":
    main()
