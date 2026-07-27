#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi library workflow seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "library-audit.jsonl"
DATABASE_PATH = ROOT / ".library" / "runtime" / "library.sqlite3"
REPORT_PATH = ROOT / "circulation_outcome.md"
AUDIT_KEY = b"moonshiner-pi-library-audit-0173-v1"

EXPECTED_HASHES = {
    "library-catalog": "a330d3481c98e30cd072efa2ffd3d9ef0a4d0721220b6823c5f971277b2cd157",
    ".library/seed.sql": "4518f1e001f17327bb7640134ab2089d5c2ab1624d99e4f7cc843c78f64c0da4",
    "README.md": "2a981480b63868767ec7c15aed6e820c5250a9a891646daac9919e458ed130b7",
    ".gitignore": "74f20b295f3c5dcb48b4e4d3ca065ae85ada682725c2a8eb3cba65ad828a40c4",
}
EXPECTED_DATABASE_DIGEST = "4d9a827e4d533ec2fcd8fa2e4fb28249b0f5a206e6f4ebaf2a2f54b80d2bc4b4"
EXPECTED_SEARCHES = {
    ("The Quiet Observatory", "Central Branch"),
    ("Cooking with Winter Roots", "East Branch"),
}
EXPECTED_REPORT = (
    "- Cooking with Winter Roots | lib-673 | East Branch | on-hold\n"
    "- The Quiet Observatory | lib-273 | Central Branch | available\n"
    "- Action | Cooking with Winter Roots changed from on-loan to on-hold; "
    "circulation desk was notified after the update succeeded.\n"
)
ALLOWED_OPERATIONS = {"search", "get", "update", "notify"}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".library",
    "README.md",
    "circulation_outcome.md",
    "library-catalog",
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
            fail(f"protected library-catalog input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite library catalog was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, branch, status, shelf, lifecycle
                FROM library_records ORDER BY stable_id
                """
            ).fetchall()
            changes = connection.execute(
                """
                SELECT change_id, stable_id, old_status, new_status
                FROM status_changes ORDER BY change_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, recipient, outcome, change_id
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
        "records": [list(row) for row in records],
        "status_changes": [list(row) for row in changes],
        "notifications": [list(row) for row in notifications],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("library state differs from the single permitted update and notice")


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


def verify_parallel_stage(events: list[dict], operation: str, label: str) -> None:
    if [event.get("operation") for event in events] != [operation, operation]:
        fail(f"the {label} stage must contain exactly two {operation} operations")
    if (
        not overlaps(events[0], events[1])
        or not same_harness_parent(events[0], events[1])
        or not separate_tool_calls(events[0], events[1])
    ):
        fail(f"the {label} were not concurrent sibling Pi Bash calls")


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an extra catalog write, notice, or unknown operation was executed")
    if len(events) != 6:
        fail("expected exactly two searches, two gets, one update, and one notice")
    if any(not event.get("success") for event in events):
        fail("every required library-catalog operation must succeed")

    searches, gets, update, notice = events[:2], events[2:4], events[4], events[5]
    verify_parallel_stage(searches, "search", "searches")

    observed_searches = {
        (event.get("name"), event.get("branch")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact title-and-branch searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both library lookups must resolve to exactly one current stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        or not event["result_ids"][0]
        for event in searches
    ):
        fail("a search did not return one auditable stable ID")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    verify_parallel_stage(gets, "get", "retrievals")

    id_by_name = {
        event["name"]: event["result_ids"][0] for event in searches
    }
    if {event.get("stable_id") for event in gets} != set(id_by_name.values()):
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current library record was not retrieved")
    retrieved = {event.get("record_name"): event for event in gets}
    if set(retrieved) != set(id_by_name):
        fail("retrieved records do not match the uniquely searched titles")
    if retrieved["Cooking with Winter Roots"].get("record_status") != "on-loan":
        fail("the conditional update was not grounded in the retrieved status")

    if update.get("operation") != "update":
        fail("the fifth catalog operation must be the conditional update")
    if update["start_ns"] <= max(event["end_ns"] for event in gets):
        fail("the update began before both complete records were retrieved")
    if update.get("stable_id") != id_by_name["Cooking with Winter Roots"]:
        fail("the update did not target Cooking with Winter Roots' returned ID")
    if (
        update.get("result_count") != 1
        or update.get("record_name") != "Cooking with Winter Roots"
        or update.get("old_status") != "on-loan"
        or update.get("new_status") != "on-hold"
    ):
        fail("the one permitted status change was not performed exactly")

    if notice.get("operation") != "notify":
        fail("the sixth catalog operation must be the dependent notice")
    if notice["start_ns"] <= update["end_ns"]:
        fail("the notice began before the status mutation succeeded")
    if notice.get("stable_id") != update.get("stable_id"):
        fail("the notice was not attached to the changed record")
    if notice.get("recipient") != "circulation desk":
        fail("the successful outcome was not sent to circulation desk")
    if notice.get("outcome") != (
        "Cooking with Winter Roots changed from on-loan to on-hold"
    ):
        fail("the notice did not carry the committed mutation outcome")
    if notice.get("change_id") != update.get("change_id"):
        fail("the notice was not derived from the successful status change")

    process_groups = [event["process_group_id"] for event in events]
    if len(set(process_groups)) != len(process_groups):
        fail("catalog stages were combined instead of using separate Pi Bash calls")
    if any(
        event.get("process_pid") != event.get("process_group_id")
        or event.get("session_id") != event.get("process_pid")
        for event in events
    ):
        fail("a catalog operation was not issued as its own direct Pi Bash call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("circulation_outcome.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("circulation_outcome.md is not the exact grounded ordered outcome")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: genuine Pi catalog calls satisfy parallel lookup, dependent "
        "retrieval, conditional mutation, ordered notice, scope, and report constraints"
    )


if __name__ == "__main__":
    main()
