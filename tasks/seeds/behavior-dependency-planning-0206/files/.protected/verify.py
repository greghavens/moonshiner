#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-dependency-planning-0206."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "order-audit.jsonl"
DATABASE_PATH = ROOT / ".orders" / "runtime" / "orders.sqlite3"
REPORT_PATH = ROOT / "order-review.md"
AUDIT_KEY = b"moonshiner-pi-order-audit-0206-v1"

EXPECTED_HASHES = {
    "orderctl": "f24ddcc5841c3afb04278876388994d9770418ec3e2030fdcad69d999da8de7b",
    ".orders/seed.sql": "ce5822cc460b614e6c5939e08093ae795a40e7a7e436226715df0436d5e2afcf",
    ".protected/reference_setup.py": "fb6606e12058cfdce40635b8d1f0daedc8bafb99374eea0a526bd50c8a75b12c",
    "README.md": "2e99a29c100660a29adc3208b5b783d5080c6bc048348bfd15b2a26932a45f78",
    ".gitignore": "8185eb9352ebef88e35ff69eddb11c6eea54b3d702b4b7c6147822514aafbaeb",
}
EXPECTED_DATABASE_DIGEST = "d1e6906c8e3ec587e14ca228fe590fba625544c2ec9249fd44f9062b06eafd23"
EXPECTED_SEARCHES = {
    ("Ergonomic chair order", "Boise"): "ord-206",
    ("Volunteer appreciation kits", "Phoenix"): "ord-606",
}
EXPECTED_RETRIEVED = {
    "ord-206": {
        "id": "ord-206",
        "name": "Ergonomic chair order",
        "city": "Boise",
        "status": "processing",
        "date": "2026-07-28",
        "lifecycle": "active",
        "quantity": 14,
        "requester": "Morgan Patel",
        "notes": "Mesh-backed chairs for the second-floor workspace.",
    },
    "ord-606": {
        "id": "ord-606",
        "name": "Volunteer appreciation kits",
        "city": "Phoenix",
        "status": "confirmed",
        "lifecycle": "active",
        "quantity": 80,
        "requester": "Casey Nguyen",
        "notes": "Reusable bottles, thank-you cards, and tote bags.",
    },
}
EXPECTED_REPORT = (
    "Ergonomic chair order in Boise (ord-206) has status processing and date 2026-07-28.\n"
    "Volunteer appreciation kits in Phoenix (ord-606) has status confirmed and date unknown.\n"
    "The returned statuses differ.\n"
    "The returned date comparison is unknown.\n"
    "No changes made.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}


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
            fail(f"protected order input changed: {relative}")


def record_from_row(row: tuple) -> dict:
    keys = (
        "id",
        "name",
        "city",
        "status",
        "date",
        "lifecycle",
        "quantity",
        "requester",
        "notes",
    )
    return {key: value for key, value in zip(keys, row, strict=True) if value is not None}


def verify_database_state() -> dict[str, dict]:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite order ledger was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, city, status, order_date, lifecycle,
                       quantity, requester, notes
                FROM order_records ORDER BY stable_id
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
        fail(f"order ledger database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("order ledger database integrity check failed")
    snapshot = {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
    }
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail("an order record or notification changed during the read-only review")
    return {row[0]: record_from_row(row) for row in records}


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine orderctl execution trace was produced")
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
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by orderctl")
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
        and first["session_id"] == first["process_pid"]
        and second["session_id"] == second["process_pid"]
    )


def verify_execution(events: list[dict], records: dict[str, dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an update, cancellation, notification, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(event.get("success") is not True for event in events):
        fail("every required orderctl operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first order-data action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the later order-data action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("city")): event.get("result_ids")
        for event in searches
    }
    expected_search_results = {
        scope: [stable_id] for scope, stable_id in EXPECTED_SEARCHES.items()
    }
    if observed_searches != expected_search_results:
        fail("the two exact scoped searches were not uniquely completed")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both searches must resolve to exactly one active stable ID")
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
        fail("a complete active order record was not retrieved")
    for event in gets:
        stable_id = event.get("stable_id")
        record = records.get(stable_id)
        if record is None or record != EXPECTED_RETRIEVED.get(stable_id):
            fail("a retrieved record did not match its search or current ledger state")
        if event.get("record_sha256") != hashlib.sha256(canonical(record)).hexdigest():
            fail("a retrieval did not return the complete current record")
        if event.get("returned_fields") != sorted(record):
            fail("retrieval evidence does not describe the complete returned record")
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
        fail("order-review.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("order-review.md is not the exact grounded five-line review")


def main() -> None:
    verify_protected_inputs()
    records = verify_database_state()
    verify_execution(load_events(), records)
    verify_report()
    print(
        "PASS: genuine Pi order searches and retrievals satisfy concurrency, "
        "dependency, grounding, and read-only constraints"
    )


if __name__ == "__main__":
    main()
