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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "catalog-audit.jsonl"
DATABASE_PATH = ROOT / ".library" / "runtime" / "catalog.sqlite3"
REPORT_PATH = ROOT / "status_audit.md"
AUDIT_KEY = b"moonshiner-pi-library-audit-0093-v1"

EXPECTED_HASHES = {
    "library-catalog": "f02ca4065a37ece271f04803626ebda41b5fdb19c439a1952d4635161733ffeb",
    ".library/seed.sql": "12a89e6a64fffd6610a8a845bd44713fe19ff0acd98e5fce8a1138cedc3306eb",
    "README.md": "07152d1ed90d1a89f76bfb3dd956344047bd88392cdbb366b1301ac1c4c0bb4a",
    ".gitignore": "24b67087c8264f5976c317191d3de8ece9e12cbc580da28af348f3c0a607bbb3",
}
EXPECTED_DATABASE_DIGEST = "4de12b5ee930eb6f3ea3dda58a6cec12a614fb136f4d537c845ff6aab907a09c"
EXPECTED_SEARCHES = {
    ("Tidepool Field Guide", "Central Branch"),
    ("The Cartographer's Lantern", "East Branch"),
}
EXPECTED_REPORT = (
    "- The Cartographer's Lantern: on-loan.\n"
    "- Tidepool Field Guide: available.\n"
    "- Comparison: The returned statuses differ.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
EXPECTED_WORKSPACE_ENTRIES = {
    ".gitignore",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/catalog-audit.jsonl",
    ".harness/verify.py",
    ".library",
    ".library/runtime",
    ".library/runtime/catalog.sqlite3",
    ".library/runtime/initialize.lock",
    ".library/seed.sql",
    "README.md",
    "library-catalog",
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
            fail(f"protected catalog input changed: {relative}")


def verify_workspace_entries() -> None:
    observed = {
        relative.as_posix()
        for path in ROOT.rglob("*")
        if (relative := path.relative_to(ROOT)).parts[0] != ".git"
    }
    unexpected = sorted(observed - EXPECTED_WORKSPACE_ENTRIES)
    if unexpected:
        fail("unexpected scratch artifact: " + unexpected[0])


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite library catalog was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            titles = connection.execute(
                """
                SELECT stable_id, title, branch, status, lifecycle
                FROM titles ORDER BY stable_id
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
        fail(f"library catalog database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("library catalog database integrity check failed")
    snapshot = {
        "titles": [list(row) for row in titles],
        "notifications": [list(row) for row in notifications],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("library catalog state changed during the read-only audit")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine library-catalog execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by the catalog")
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
        fail("every required library-catalog operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first catalog action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next catalog action must contain only both retrievals")

    observed_searches = {
        (event.get("title"), event.get("branch")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact title-and-branch searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both lookups must resolve to exactly one current stable ID")
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
        fail("a complete current library record was not retrieved")
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
        fail("status_audit.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("status_audit.md is not the exact grounded three-bullet audit")


def main() -> None:
    verify_protected_inputs()
    verify_workspace_entries()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded library status audit and genuine Pi catalog execution "
        "satisfy every dependency, concurrency, ordering, and read-only constraint"
    )


if __name__ == "__main__":
    main()
