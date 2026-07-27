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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "account-audit.jsonl"
DATABASE_PATH = ROOT / ".accounts" / "runtime" / "accounts.sqlite3"
REPORT_PATH = ROOT / "account_comparison.md"
AUDIT_KEY = b"moonshiner-pi-account-audit-0229-v1"

EXPECTED_HASHES = {
    "account-registry": "24f45fadc105e9d4979edd50e36761520102d420ae210bfb9404c1546fe9142f",
    ".accounts/seed.sql": "da1e4f398b3716497ae837d7124c0d725415dd999707e2ffc83244cc4c2cc24f",
    "README.md": "72fc6ceb050529adbcf5535826752b7aaf9ec2222206c3c9f2a6ea6b7e1b826a",
    ".gitignore": "6c87c460ff073195fdf5140724bed9387ac05f67971403fba78617d228b1a861",
}
EXPECTED_DATABASE_DIGEST = "719d6703714235408d0fad56c4e223c3e9210eee9393044e2f354091d6d464eb"
EXPECTED_SEARCHES = {
    ("Cobalt Museum sponsorship", "Northeast"),
    ("Delta Housing expansion", "Southwest"),
}
ALLOWED_OPERATIONS = {"search", "get"}
REQUIRED_RECORD_FIELDS = {
    "name",
    "stable_id",
    "region",
    "status",
    "date",
    "account_owner",
    "commitment_cents",
    "contact",
    "notes",
    "lifecycle",
}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".accounts",
    ".reference_solution",
    "README.md",
    "account-registry",
    "account_comparison.md",
}


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
            fail(f"protected account-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at sandbox root: " + ", ".join(unexpected))


def database_snapshot() -> tuple[dict, dict[str, dict]]:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite account registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            rows = connection.execute(
                """
                SELECT stable_id, name, region, status, record_date, account_owner,
                       commitment_cents, contact, notes, lifecycle
                FROM account_records ORDER BY stable_id
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
        fail(f"account registry database is unreadable: {exc}")

    if integrity is None or integrity[0] != "ok":
        fail("account registry database integrity check failed")
    records = [list(row) for row in rows]
    snapshot = {
        "records": records,
        "notifications": [list(row) for row in notifications],
    }
    records_by_id = {
        row[0]: {
            "stable_id": row[0],
            "name": row[1],
            "region": row[2],
            "status": row[3],
            "date": row[4],
            "account_owner": row[5],
            "commitment_cents": row[6],
            "contact": row[7],
            "notes": row[8],
            "lifecycle": row[9],
        }
        for row in records
    }
    return snapshot, records_by_id


def verify_database_state() -> dict[str, dict]:
    snapshot, records_by_id = database_snapshot()
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail(
            "account registry state changed; targets, similarly named, related, "
            "archived, other accounts, and notifications must remain untouched"
        )
    return records_by_id


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine account-registry execution trace was produced")
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


def verify_execution(events: list[dict], records_by_id: dict[str, dict]) -> set[str]:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("a create, update, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required account-registry operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("region")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-region searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both account lookups must resolve to exactly one current stable ID")
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
        fail("a complete current account record was not retrieved")
    for event in gets:
        stable_id = event.get("stable_id")
        record = records_by_id.get(stable_id)
        if record is None or set(record) != REQUIRED_RECORD_FIELDS:
            fail("a retrieval did not resolve to a complete managed account record")
        if event.get("field_count") != len(REQUIRED_RECORD_FIELDS):
            fail("a retrieval did not return every complete-record field")
        if event.get("record_digest") != hashlib.sha256(canonical(record)).hexdigest():
            fail("a retrieval result was not the complete current managed record")
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
    return returned_ids


def verify_report(records_by_id: dict[str, dict], returned_ids: set[str]) -> None:
    ordered = sorted(
        (records_by_id[stable_id] for stable_id in returned_ids),
        key=lambda record: record["name"],
    )
    if len(ordered) != 2:
        fail("the report is not based on exactly two retrieved accounts")
    first, second = ordered
    status_relation = "match" if first["status"] == second["status"] else "differ"
    date_relation = "match" if first["date"] == second["date"] else "differ"
    expected = (
        f'- {first["name"]} | {first["region"]} | ID {first["stable_id"]} | '
        f'status {first["status"]} | date {first["date"]}\n'
        f'- {second["name"]} | {second["region"]} | ID {second["stable_id"]} | '
        f'status {second["status"]} | date {second["date"]}\n'
        f'- Comparison | statuses {status_relation} '
        f'({first["status"]} vs {second["status"]}) | dates {date_relation} '
        f'({first["date"]} vs {second["date"]})\n'
    )
    if not REPORT_PATH.is_file():
        fail("account_comparison.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected:
        fail("account_comparison.md is not the exact grounded alphabetical comparison")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    records_by_id = verify_database_state()
    returned_ids = verify_execution(load_events(), records_by_id)
    verify_report(records_by_id, returned_ids)
    print(
        "PASS: grounded alphabetical account comparison and genuine Pi registry "
        "execution satisfy every dependency, concurrency, scope, and read-only constraint"
    )


if __name__ == "__main__":
    main()
