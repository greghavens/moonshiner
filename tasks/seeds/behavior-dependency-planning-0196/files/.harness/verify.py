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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "claim-audit.jsonl"
DATABASE_PATH = ROOT / ".claims_data" / "runtime" / "claims.sqlite3"
REPORT_PATH = ROOT / "claim-handoff.md"
AUDIT_KEY = b"moonshiner-pi-claim-audit-0196-v1"

EXPECTED_HASHES = {
    "claim-registry": "1ca3470129220171ee24f449df30bb8fdb09e72b5b55c32f18f40a6824620334",
    ".harness/reference_setup.py": "c6d107a3638af23450f0e524952225eb9e641991904903572c44e4aa517fef21",
    ".claims_data/seed.sql": "4b29a29b2e6be4554d8d8f4e3c06893d95d3046c217b566496023611f64b7e23",
    "README.md": "db04dc3a37b633b5ecbd7a1ad5bbff8ab2b58df466aa38cdef0df3e807136183",
    ".gitignore": "d4cbca4c13dca2f091257e46e2281a925cad9ab88d56def7c2dce86dd83c355e",
}
EXPECTED_MODES = {
    "claim-registry": 0o755,
    ".harness/reference_setup.py": 0o755,
    ".claims_data/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".claims_data",
    ".claims_data/runtime",
    ".harness",
    ".harness/runtime",
}
EXPECTED_FILES = {
    ".claims_data/runtime/initialize.lock",
    ".claims_data/runtime/claims.sqlite3",
    ".claims_data/seed.sql",
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/runtime/claim-audit.jsonl",
    ".harness/verify.py",
    "README.md",
    "claim-registry",
    "claim-handoff.md",
}
EXPECTED_SCHEMA_DIGEST = "d4c776dd9e4edcb2507a04838199bf5a6a8b6b1d5188a57cb9f2b69026e0d8c7"
EXPECTED_DATABASE_DIGEST = "5dc191f228a46b1978fd2954fc356fe3443ff1107f5753d3a4bf9397c2d6b7bf"
EXPECTED_SEARCH_IDS = {
    ("Theft claim — gallery camera", "West Office"): "ins-296",
    ("Windshield claim — fleet van", "North Office"): "ins-696",
}
EXPECTED_REPORT = (
    "- Theft claim — gallery camera | ID: ins-296 | office: West Office | "
    "status: adjuster-assigned | date: 2026-08-14\n"
    "- Windshield claim — fleet van | ID: ins-696 | office: North Office | "
    "status: documentation-needed | date: 2026-08-16\n"
    "- Comparison | status: different (adjuster-assigned vs "
    "documentation-needed) | date: different (2026-08-14 vs 2026-08-16)\n"
)
EXPECTED_RECORDS = {
    "ins-296": {
        "name": "Theft claim — gallery camera",
        "stable_id": "ins-296",
        "office": "West Office",
        "status": "adjuster-assigned",
        "date": "2026-08-14",
        "policyholder": "Juniper Gallery",
        "claim_type": "commercial-property",
        "lifecycle": "current",
    },
    "ins-696": {
        "name": "Windshield claim — fleet van",
        "stable_id": "ins-696",
        "office": "North Office",
        "status": "documentation-needed",
        "date": "2026-08-16",
        "policyholder": "Northwind Couriers",
        "claim_type": "commercial-auto",
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
            fail(f"protected claim-registry input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected claim-registry input mode changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite claim registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            claims = connection.execute(
                """
                SELECT stable_id, name, office, status, claim_date,
                       policyholder, claim_type, lifecycle
                FROM claims ORDER BY stable_id
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
        fail(f"claim registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("claim registry database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("claim registry schema changed")
    snapshot = {
        "claims": [list(row) for row in claims],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sqlite_sequence],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "claim registry state changed; requested, distractor, archived, "
            "and notification records must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine claim-registry execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by claim-registry")
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
        fail("an update, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required claim-registry operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("office")): event for event in searches
    }
    if set(observed_searches) != set(EXPECTED_SEARCH_IDS):
        fail("the two required exact name-and-office searches were not run")
    for lookup, expected_id in EXPECTED_SEARCH_IDS.items():
        event = observed_searches[lookup]
        if event.get("result_count") != 1:
            fail("both claim lookups must resolve to one current stable ID")
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
        fail("a complete current claim record was not retrieved")
    for event in gets:
        record = EXPECTED_RECORDS[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail("a retrieved claim record was not returned completely and intact")
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
        fail("claim-handoff.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("claim-handoff.md is not the exact grounded read-only comparison")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded claim comparison and genuine Pi registry execution "
        "satisfy dependency, concurrency, read-only, and exact-format constraints"
    )


if __name__ == "__main__":
    main()
