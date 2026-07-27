#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "shipment-audit.jsonl"
DATABASE_PATH = ROOT / ".shipments" / "runtime" / "shipments.sqlite3"
REPORT_PATH = ROOT / "shipment_handoff.md"
AUDIT_KEY = b"moonshiner-pi-shipment-audit-0208-v1"

EXPECTED_HASHES = {
    "shipment-ledger": "ab2077c0459efe86d6a9aacccf5c9e159cc7274b9ad9081d876b48ab57479071",
    ".harness/reference_setup.py": "d5822044056386440ae12aa0232c84702ff7150a0cf1d84ae22b5f4bf5b1b5cf",
    ".shipments/seed.sql": "a0feca82ee182d23e0871e2a1dcd58823211d8902dd2fd53f80362244069faee",
    "README.md": "ef6d70025279256754be0229511caac4ba1939872fe950a2e37db6e5fdcf5a3c",
    ".gitignore": "c813a7ddac274145f0a854bed6c71a99f4db67521b2f67d7d795d81a091d1481",
}
EXPECTED_MODES = {
    "shipment-ledger": 0o755,
    ".harness/reference_setup.py": 0o755,
    ".shipments/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".harness",
    ".harness/runtime",
    ".shipments",
    ".shipments/runtime",
}
EXPECTED_FILES = {
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/runtime/shipment-audit.jsonl",
    ".harness/verify.py",
    ".shipments/runtime/initialize.lock",
    ".shipments/runtime/shipments.sqlite3",
    ".shipments/seed.sql",
    "README.md",
    "shipment-ledger",
    "shipment_handoff.md",
}
EXPECTED_SCHEMA_DIGEST = "b0d463e98647908c056d3388fb0f7c8f011d8ce5fbefbe5c82efd35a146e5b93"
EXPECTED_DATABASE_DIGEST = "9e4c7c2ec498944836b2810cd08052c38c7927569c92f8fdaa198e35377e3951"
EXPECTED_SEARCH_IDS = {
    ("Library transfer cartons", "Portland"): "shp-208",
    ("Vaccine cooler shipment", "Denver"): "shp-608",
}
EXPECTED_REPORT = (
    "Library transfer cartons in Portland (shp-208) has status in-transit "
    "and date 2026-07-24.\n"
    "Vaccine cooler shipment in Denver (shp-608) has status delayed and date "
    "2026-07-23.\n"
    "Comparison: statuses differ (in-transit versus delayed), and dates "
    "differ (2026-07-24 versus 2026-07-23).\n"
)
EXPECTED_RECORDS = {
    "shp-208": {
        "shipment": "Library transfer cartons",
        "stable_id": "shp-208",
        "city": "Portland",
        "status": "in-transit",
        "date": "2026-07-24",
        "carrier": "Northstar Freight",
        "service": "regional ground",
        "lifecycle": "current",
    },
    "shp-608": {
        "shipment": "Vaccine cooler shipment",
        "stable_id": "shp-608",
        "city": "Denver",
        "status": "delayed",
        "date": "2026-07-23",
        "carrier": "Alpine Medical Logistics",
        "service": "cold-chain priority",
        "lifecycle": "current",
    },
}
ALLOWED_OPERATIONS = {"search", "get"}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
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
            fail(f"protected shipment-ledger input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected shipment-ledger input mode changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite shipment ledger was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            shipments = connection.execute(
                """
                SELECT stable_id, shipment, city, status, shipment_date,
                       carrier, service, lifecycle
                FROM shipments ORDER BY stable_id
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
            sqlite_sequence = connection.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"shipment ledger database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("shipment ledger database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("shipment ledger schema changed")
    snapshot = {
        "shipments": [list(row) for row in shipments],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sqlite_sequence],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "shipment ledger state changed; requested, related, draft, "
            "archived, distractor, and notification records must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine shipment-ledger execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by shipment-ledger")
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
        fail("every required shipment-ledger operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first ledger action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next ledger action must contain only both retrievals")

    observed_searches = {
        (event.get("shipment"), event.get("city")): event for event in searches
    }
    if set(observed_searches) != set(EXPECTED_SEARCH_IDS):
        fail("the two required exact shipment-and-city searches were not run")
    for lookup, expected_id in EXPECTED_SEARCH_IDS.items():
        event = observed_searches[lookup]
        if event.get("result_count") != 1:
            fail("both shipment lookups must resolve to exactly one current stable ID")
        if event.get("result_ids") != [expected_id]:
            fail("a search did not return its one protected current stable ID")
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
    returned_ids = set(EXPECTED_SEARCH_IDS.values())
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current shipment record was not retrieved")
    for event in gets:
        record = EXPECTED_RECORDS[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail("a retrieved shipment record was not returned completely and intact")
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
        fail("shipment_handoff.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("shipment_handoff.md is not the exact grounded read-only comparison")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded shipment comparison and genuine Pi ledger execution "
        "satisfy dependency, concurrency, scope, and read-only constraints"
    )


if __name__ == "__main__":
    main()
