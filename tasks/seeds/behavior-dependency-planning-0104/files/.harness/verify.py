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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "message-audit.jsonl"
DATABASE_PATH = ROOT / ".messages" / "runtime" / "messages.sqlite3"
REPORT_PATH = ROOT / "status_audit.md"
AUDIT_KEY = b"moonshiner-pi-message-audit-0104-v1"

EXPECTED_HASHES = {
    "message-registry": "6f52a414cc31f399a62bd27537d71c02069c7f0cd8e691bbbe7a608dfa0ee0f2",
    ".messages/seed.sql": "868c331c1cb9f94af9a18e8f50de9db35aa1d47d4fe76b3351eb62097beed115",
    "README.md": "a4e3130054586adba3cf387df634bd454704ed86a3bd0c4c3196a3a87bad3c38",
    ".gitignore": "d942c9b2b3288334f456d60a63cb24e31ebe8770819f22edc3b7571cc0074040",
}
EXPECTED_DATABASE_DIGEST = "3e4a1530a6ad5eb5a09435ef5abf862164999274a3557c0517a09e85121ab3b8"
EXPECTED_SEARCHES = {
    ("Volunteer renewal reminder", "Volunteers"),
    ("North team quarterly update", "North Team"),
}
EXPECTED_REPORT = (
    "Volunteer renewal reminder in Volunteers has status sent.\n"
    "North team quarterly update in North Team has status draft.\n"
    "The returned statuses differ.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_WORKSPACE_ENTRIES = {
    ".gitignore",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/message-audit.jsonl",
    ".harness/verify.py",
    ".messages",
    ".messages/runtime",
    ".messages/runtime/initialize.lock",
    ".messages/runtime/messages.sqlite3",
    ".messages/seed.sql",
    "README.md",
    "message-registry",
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
            fail(f"protected message-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] == ".git":
            continue
        if path.is_symlink() or relative.as_posix() not in ALLOWED_WORKSPACE_ENTRIES:
            unexpected.append(relative.as_posix())
    if unexpected:
        fail("unexpected scratch artifact in workspace: " + ", ".join(sorted(unexpected)))


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite message registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, location, status, channel, scheduled_for,
                       body, lifecycle
                FROM message_records ORDER BY stable_id
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
        fail(f"message registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("message registry database integrity check failed")
    snapshot = {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "message registry state changed; target, similarly named, related, "
            "archived, other records, and notifications must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine message-registry execution trace was produced")
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
        fail("every required message-registry operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("location")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-location searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both messaging lookups must resolve to exactly one current stable ID")
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
        fail("a complete current message record was not retrieved")
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
        "PASS: grounded message status audit and genuine Pi registry execution "
        "satisfy every dependency, concurrency, scope, and read-only constraint"
    )


if __name__ == "__main__":
    main()
