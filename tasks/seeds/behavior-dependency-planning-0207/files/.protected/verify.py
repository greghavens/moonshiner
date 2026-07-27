#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-dependency-planning-0207."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "inventory-audit.jsonl"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
DATABASE_PATH = ROOT / ".inventory" / "runtime" / "inventory.sqlite3"
REPORT_PATH = ROOT / "audit-note.md"

EXPECTED_HASHES = {
    "inventoryctl": "5d97f834de2d62e5a926e9cae505d6090459808180c8e070d480eb49d97fbb99",
    ".inventory/seed.sql": "6059efb5f30e9be017dff62e082ef6a1021c9d6cb935a16efbadb36b175f1db6",
    ".protected/audit.key": "d565a457849187dc6d2a675c8e09106a78de9130b6da55e3f2bb7e7a8492257f",
    ".protected/reference_setup.py": "7f338c13f512346f1283e3b7ef333329034d1d08b70e8e1ec0f0103a9c3a5e90",
    "README.md": "eacdd3b1e3c2626068d0dba12553110c8de620dc35de78d4ea07eec6ea2b58cd",
    ".gitignore": "911b945f77a5c74472ee8f4e1be860b9d284aac45cd040aee4fe6ba66d85aa91",
}
EXPECTED_MODES = {
    "inventoryctl": 0o755,
    ".inventory/seed.sql": 0o644,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".inventory",
    ".inventory/runtime",
    ".protected",
    ".protected/runtime",
}
EXPECTED_FILES = {
    ".gitignore",
    ".inventory/runtime/initialize.lock",
    ".inventory/runtime/inventory.sqlite3",
    ".inventory/seed.sql",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/inventory-audit.jsonl",
    ".protected/verify.py",
    ".reference_solution",
    "README.md",
    "audit-note.md",
    "inventoryctl",
}
EXPECTED_SCHEMA_DIGEST = "53ad35ec49b8c919def1f96a7da50932154d224e24fb38a028ff1cafcb7594e3"
EXPECTED_DATABASE_DIGEST = "d93ec77f5d7f8d7a240255340d0ff19a59d95b41b5ece19e9578b8e01035a333"
EXPECTED_SEARCHES = {
    ("Acid-free archive folders", "Warehouse C"): "itm-207",
    ("Shipping label rolls", "Warehouse D"): "itm-807",
}
EXPECTED_RECORDS = {
    "itm-207": {
        "id": "itm-207",
        "name": "Acid-free archive folders",
        "warehouse": "Warehouse C",
        "status": "stocked",
        "date": "2026-07-12",
        "lifecycle": "current",
        "sku": "ARC-FLD-AF",
        "quantity": 240,
        "owner": "Records Supply",
        "notes": "Buffered folders reserved for the municipal archive intake.",
    },
    "itm-807": {
        "id": "itm-807",
        "name": "Shipping label rolls",
        "warehouse": "Warehouse D",
        "status": "backordered",
        "date": "2026-05-21",
        "lifecycle": "current",
        "sku": "SHP-LBL-4X6",
        "quantity": 0,
        "owner": "Fulfillment Supply",
        "notes": "Vendor backorder remains open after the packing-line change.",
    },
}
EXPECTED_REPORT = (
    "- Acid-free archive folders | ID: itm-207 | warehouse: Warehouse C | "
    "status: stocked | date: 2026-07-12\n"
    "- Shipping label rolls | ID: itm-807 | warehouse: Warehouse D | "
    "status: backordered | date: 2026-05-21\n"
    "- Comparison | status: different (stocked vs backordered) | date: "
    "different (2026-07-12 vs 2026-05-21)\n"
    "- Recommendation | cancellation candidate: Shipping label rolls "
    "(itm-807); explicit confirmation required before any change; no changes made.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_workspace_shape() -> None:
    for path in ROOT.rglob("*"):
        relative_path = path.relative_to(ROOT)
        if relative_path.parts[0] == ".git":
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            fail(f"unexpected scratch artifact or altered path: {relative}")
        if path.is_dir():
            if relative not in EXPECTED_DIRECTORIES:
                fail(f"unexpected scratch directory remains: {relative}")
        elif path.is_file():
            if relative not in EXPECTED_FILES:
                fail(f"unexpected scratch file remains: {relative}")
        else:
            fail(f"unexpected scratch artifact remains: {relative}")


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected inventory input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected inventory input mode changed: {relative}")


def record_from_row(row: tuple) -> dict:
    keys = (
        "id",
        "name",
        "warehouse",
        "status",
        "date",
        "lifecycle",
        "sku",
        "quantity",
        "owner",
        "notes",
    )
    return {key: value for key, value in zip(keys, row, strict=True)}


def verify_database_state() -> dict[str, dict]:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite inventory ledger was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, warehouse, status, item_date, lifecycle,
                       sku, quantity, owner, notes
                FROM item_records ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
            schema = connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_schema ORDER BY type, name
                """
            ).fetchall()
            sequence = connection.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"inventory ledger database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("inventory ledger database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("inventory ledger schema changed")
    snapshot = {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sequence],
    }
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail("an inventory item or notification changed during the read-only audit")
    return {row[0]: record_from_row(row) for row in records}


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine inventoryctl execution trace was produced")
    audit_key = AUDIT_KEY_PATH.read_bytes().rstrip(b"\n")
    events: list[dict] = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            signed_event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(signed_event, dict):
            fail(f"audit event {number} is not an object")
        event = dict(signed_event)
        signature = event.pop("signature", None)
        expected = hmac.new(audit_key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by inventoryctl")
        for field in (
            "start_ns",
            "end_ns",
            "process_pid",
            "process_group_id",
            "session_id",
            "parent_pid",
        ):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        if not isinstance(event.get("event_id"), str) or not event["event_id"]:
            fail(f"audit event {number} has no event ID")
        events.append(event)
    if len({event["event_id"] for event in events}) != len(events):
        fail("the inventory execution trace contains duplicate event IDs")
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
        and first["session_id"] == first["process_pid"]
        and second["session_id"] == second["process_pid"]
    )


def verify_execution(events: list[dict], records: dict[str, dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an update, cancellation, notification, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(event.get("success") is not True for event in events):
        fail("every required inventoryctl operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first inventory action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the later inventory action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("warehouse")): event.get("result_ids")
        for event in searches
    }
    expected_search_results = {
        scope: [stable_id] for scope, stable_id in EXPECTED_SEARCHES.items()
    }
    if observed_searches != expected_search_results:
        fail("the two exact scoped searches were not uniquely completed")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both searches must resolve to exactly one current stable ID")
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
    returned_ids = set(EXPECTED_SEARCHES.values())
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current item record was not retrieved")
    for event in gets:
        stable_id = event.get("stable_id")
        record = records.get(stable_id)
        if record is None or record != EXPECTED_RECORDS.get(stable_id):
            fail("a retrieved item did not match its search or current ledger state")
        if event.get("record_sha256") != hashlib.sha256(canonical(record)).hexdigest():
            fail("a retrieval did not return the complete current item record")
        if event.get("returned_fields") != sorted(record):
            fail("retrieval evidence does not describe all complete-record fields")
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
        fail("audit-note.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("audit-note.md is not the exact grounded four-bullet audit note")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    records = verify_database_state()
    verify_execution(load_events(), records)
    verify_report()
    print(
        "PASS: genuine Pi inventory searches and retrievals satisfy concurrency, "
        "dependency, grounding, read-only, and confirmation constraints"
    )


if __name__ == "__main__":
    main()
