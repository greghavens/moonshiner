#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi CRM dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "account-audit.jsonl"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
DATABASE_PATH = ROOT / ".accounts" / "runtime" / "accounts.sqlite3"
SEED_PATH = ROOT / ".accounts" / "seed.sql"
REPORT_PATH = ROOT / "account-check.md"

EXPECTED_HASHES = {
    "account-registry": "4e60d80f00255181a748c343ae85a46c224a898c79cabc4035f1b33f3b6aeb49",
    ".protected/audit.key": "d4a9e3618134f5eb42956625adb36345e0e545cc89d1c4d038e709b7f54950b8",
    ".protected/reference_setup.py": "df6f945d7d53ead1474d63cc90890f82b3c671853978c90288d1105111628dc2",
    ".accounts/seed.sql": "ca554e79412829ff050a6987ddca363598273c9eaf5110e30e23e058758843c7",
    "README.md": "452596276e3e31e729f4cda9180d91eaf1aefd3fa5243cfe337174420a99b481",
    ".gitignore": "b0b1f57c7d4c0bde57a43b4b001c3d00ef86019e09c340531e51c28df5f9c206",
}
EXPECTED_MODES = {
    "account-registry": 0o755,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    ".accounts/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".accounts",
    ".accounts/runtime",
    ".protected",
    ".protected/runtime",
}
EXPECTED_FILES = {
    ".gitignore",
    ".accounts/runtime/accounts.sqlite3",
    ".accounts/runtime/initialize.lock",
    ".accounts/seed.sql",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/account-audit.jsonl",
    ".protected/verify.py",
    "README.md",
    "account-check.md",
    "account-registry",
}
TARGET_SCOPES = (
    ("Arbor Foods renewal", "West Region"),
    ("Bright Dental onboarding", "Central Region"),
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
            fail(f"supplied account-registry input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"supplied account-registry input mode changed: {relative}")


def database_snapshot(connection: sqlite3.Connection) -> dict:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity != ("ok",):
        fail("account registry database integrity check failed")
    schema = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_schema ORDER BY type, name
        """
    ).fetchall()
    accounts = connection.execute(
        """
        SELECT stable_id, name, region, status, account_date,
               owner, service_tier, details, lifecycle
        FROM accounts ORDER BY stable_id
        """
    ).fetchall()
    notifications = connection.execute(
        """
        SELECT notification_id, stable_id, message
        FROM notifications ORDER BY notification_id
        """
    ).fetchall()
    sequences = connection.execute(
        "SELECT name, seq FROM sqlite_sequence ORDER BY name"
    ).fetchall()
    return {
        "schema": [list(row) for row in schema],
        "accounts": [list(row) for row in accounts],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sequences],
    }


def expected_state() -> tuple[dict, dict[tuple[str, str], dict]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        connection.commit()
        snapshot = database_snapshot(connection)
        records: dict[tuple[str, str], dict] = {}
        for name, region in TARGET_SCOPES:
            rows = connection.execute(
                """
                SELECT name, stable_id, region, status, account_date,
                       owner, service_tier, details, lifecycle
                FROM accounts
                WHERE name = ? AND region = ? AND lifecycle = 'current'
                ORDER BY stable_id
                """,
                (name, region),
            ).fetchall()
            if len(rows) != 1:
                fail("protected seed no longer has one current target per scope")
            row = rows[0]
            records[(name, region)] = {
                "name": row[0],
                "stable_id": row[1],
                "region": row[2],
                "status": row[3],
                "date": row[4],
                "owner": row[5],
                "service_tier": row[6],
                "details": row[7],
                "lifecycle": row[8],
            }
        return snapshot, records
    finally:
        connection.close()


def verify_database_state(expected: dict) -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite account registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            actual = database_snapshot(connection)
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"account registry database is unreadable: {exc}")
    if actual != expected:
        fail("account or notification state changed during the read-only check")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine account-registry execution trace was produced")
    events = []
    audit_key = AUDIT_KEY_PATH.read_bytes().rstrip(b"\n")
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
        expected_signature = hmac.new(
            audit_key, canonical(event), hashlib.sha256
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected_signature
        ):
            fail(f"audit event {number} was not emitted intact by account-registry")
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
        first["process_pid"] != second["process_pid"]
        and first["process_group_id"] != second["process_group_id"]
        and first["process_pid"] == first["process_group_id"]
        and second["process_pid"] == second["process_group_id"]
        and first["session_id"] == first["process_pid"]
        and second["session_id"] == second["process_pid"]
    )


def verify_execution(events: list[dict], records: dict[tuple[str, str], dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an update, cancellation, notification, or unknown operation ran")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required account-registry operation must succeed")

    searches, retrievals = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in retrievals] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    expected_ids = {
        scope: record["stable_id"] for scope, record in records.items()
    }
    observed_searches = {
        (event.get("name"), event.get("region")): event for event in searches
    }
    if set(observed_searches) != set(expected_ids):
        fail("the two required exact name-and-region searches were not run")
    for scope, stable_id in expected_ids.items():
        event = observed_searches[scope]
        if event.get("result_count") != 1 or event.get("result_ids") != [stable_id]:
            fail("both account searches must return one exact current stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not concurrent sibling Pi Bash tool calls")

    if min(event["start_ns"] for event in retrievals) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    records_by_id = {record["stable_id"]: record for record in records.values()}
    if {event.get("stable_id") for event in retrievals} != set(records_by_id):
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in retrievals):
        fail("a complete current account record was not retrieved")
    for event in retrievals:
        record = records_by_id[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail("a retrieved account record was not returned completely and intact")
    if (
        not overlaps(retrievals[0], retrievals[1])
        or not same_harness_parent(retrievals[0], retrievals[1])
        or not separate_tool_calls(retrievals[0], retrievals[1])
    ):
        fail("the retrievals were not concurrent sibling Pi Bash tool calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in retrievals
    }:
        fail("search and retrieval stages reused a Bash tool call")


def expected_report(records: dict[tuple[str, str], dict]) -> str:
    first = records[TARGET_SCOPES[0]]
    second = records[TARGET_SCOPES[1]]
    status_relation = "match" if first["status"] == second["status"] else "differ"
    date_relation = "match" if first["date"] == second["date"] else "differ"
    return (
        f"{first['name']} in {first['region']} ({first['stable_id']}) has status "
        f"{first['status']} and date {first['date']}.\n"
        f"{second['name']} in {second['region']} ({second['stable_id']}) has status "
        f"{second['status']} and date {second['date']}.\n"
        f"Comparison: statuses {status_relation} ({first['status']} versus "
        f"{second['status']}), and dates {date_relation} ({first['date']} versus "
        f"{second['date']}).\n"
    )


def verify_report(records: dict[tuple[str, str], dict]) -> None:
    if not REPORT_PATH.is_file():
        fail("account-check.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected_report(records):
        fail("account-check.md is not the exact grounded read-only comparison")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    expected, records = expected_state()
    verify_database_state(expected)
    verify_execution(load_events(), records)
    verify_report(records)
    print(
        "PASS: grounded CRM comparison and genuine Pi registry execution satisfy "
        "dependency, concurrency, and read-only constraints"
    )


if __name__ == "__main__":
    main()
