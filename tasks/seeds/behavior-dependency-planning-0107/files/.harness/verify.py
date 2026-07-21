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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "inventory-audit.jsonl"
DATABASE_PATH = ROOT / ".inventory" / "runtime" / "inventory.sqlite3"
REPORT_PATH = ROOT / "status_audit.md"
AUDIT_KEY = b"moonshiner-pi-inventory-audit-0107-v1"

EXPECTED_HASHES = {
    "inventory-registry": "2dbfebd2f1b78082e8aa0e8eb2ae39fa9d1b8ff499dd658c47e49f53b44e1fa1",
    ".inventory/seed.sql": "b733d05cf10bfbe72b3acd77994e6e03ff057a4091a800623e0a4fd4833e5a07",
    "README.md": "c80317ce02097ec09b6caf1f02f7ae271d9b429d713aa38664bf518b9c3dcce0",
    ".gitignore": "440b26cd52f29ec9db3f834f68191735808ec9b8fd9a9376c5ea0c9e83734727",
}
EXPECTED_DATABASE_DIGEST = "2ff6c8875b8d727c26d4aa0e3d0f7e6e6fb90f04bf4369e69b9aeee960407d75"
EXPECTED_SEARCHES = {
    ("Recycled packing tape", "Warehouse C"),
    ("A4 printer paper", "Warehouse D"),
}
EXPECTED_REPORT = (
    "Recycled packing tape in Warehouse C has status available.\n"
    "A4 printer paper in Warehouse D has status reorder-pending.\n"
    "The returned statuses differ.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".inventory",
    ".reference_solution",
    "README.md",
    "inventory-registry",
    "status_audit.md",
}
ALLOWED_INVENTORY_ENTRIES = {
    "runtime",
    "runtime/initialize.lock",
    "runtime/inventory.sqlite3",
    "runtime/inventory.sqlite3-shm",
    "runtime/inventory.sqlite3-wal",
    "seed.sql",
}
ALLOWED_HARNESS_ENTRIES = {
    "reference_setup.py",
    "runtime",
    "runtime/inventory-audit.jsonl",
    "verify.py",
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
            fail(f"protected inventory-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))

    for directory, allowed in (
        (ROOT / ".inventory", ALLOWED_INVENTORY_ENTRIES),
        (ROOT / ".harness", ALLOWED_HARNESS_ENTRIES),
    ):
        nested_unexpected = sorted(
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.relative_to(directory).as_posix() not in allowed
        )
        if nested_unexpected:
            fail(
                f"unexpected scratch artifact under {directory.name}: "
                + ", ".join(nested_unexpected)
            )


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite inventory registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, warehouse, status, quantity, bin,
                       description, lifecycle
                FROM inventory_records ORDER BY stable_id
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
        fail(f"inventory registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("inventory registry database integrity check failed")
    snapshot = {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "inventory registry state changed; target, similarly named, related, "
            "archived, other items, and notifications must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine inventory-registry execution trace was produced")
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
        fail("every required inventory-registry operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("warehouse")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-warehouse searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both inventory lookups must resolve to exactly one current stable ID")
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
        fail("a complete current inventory record was not retrieved")
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
        "PASS: grounded inventory status audit and genuine Pi registry execution "
        "satisfy every dependency, concurrency, scope, and read-only constraint"
    )


if __name__ == "__main__":
    main()
