#!/usr/bin/env python3
"""Protected deterministic verifier for the support dependency task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".support-runtime"
DATABASE = RUNTIME / "support.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "shift-handoff.md"
TARGETS = (
    ("Damaged Cold-Chain Parcel", "Alder Foods"),
    ("Duplicate Annual Charge", "Beacon Arts"),
)
TABLES = (
    "cases", "saved_preferences", "availability", "notifications", "mutation_log",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return [tuple(row) for row in
            connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]


def canonical_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript((ROOT / "support_seed.sql").read_text(encoding="utf-8"))
    return connection


def expected_records(connection: sqlite3.Connection) -> list[dict[str, object]]:
    connection.row_factory = sqlite3.Row
    records: list[dict[str, object]] = []
    for name, location in TARGETS:
        matches = connection.execute(
            "SELECT id FROM cases WHERE name = ? AND location = ? ORDER BY id",
            (name, location),
        ).fetchall()
        if len(matches) != 1:
            fail("protected data no longer gives one stable ID per requested branch")
        record = connection.execute(
            "SELECT id, name, location, case_date AS date, status, "
            "priority, owner, summary FROM cases WHERE id = ?",
            (matches[0]["id"],),
        ).fetchone()
        records.append({key: record[key] for key in record.keys()})
    return records


def digest(record: dict[str, object]) -> str:
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def verify_deliverable(records: list[dict[str, object]]) -> None:
    if not DELIVERABLE.is_file():
        fail("shift-handoff.md is missing")
    ordered = sorted(records, key=lambda record: (str(record["date"]), str(record["id"])))
    lines = [
        f"- {record['date']} \u2014 {record['name']} at {record['location']} "
        f"({record['id']}): {record['status']}."
        for record in ordered
    ]
    first_status = str(ordered[0]["status"])
    second_status = str(ordered[1]["status"])
    if first_status == second_status:
        lines.append(f"Statuses match: {first_status}.")
    else:
        lines.append(f"Statuses differ: {first_status} versus {second_status}.")
    if DELIVERABLE.read_text(encoding="utf-8").splitlines() != lines:
        fail("shift-handoff.md is not the exact required date-ordered comparison")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine support executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict[str, object]]:
    if not AUDIT.is_file():
        fail("no genuine support executable audit was recorded")
    try:
        entries = [json.loads(line) for line in AUDIT.read_text(
            encoding="utf-8").splitlines()]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"support executable audit is invalid: {error}")
    if len(entries) != 4:
        fail("expected exactly two searches and two gets, with no other operations")
    return entries


def intervals_overlap(entries: list[dict[str, object]]) -> bool:
    return max(int(entry["started_ns"]) for entry in entries) < min(
        int(entry["finished_ns"]) for entry in entries
    )


def verify_trace(
    entries: list[dict[str, object]], records: list[dict[str, object]]
) -> None:
    searches = [entry for entry in entries if entry.get("operation") == "search"]
    gets = [entry for entry in entries if entry.get("operation") == "get"]
    if len(searches) != 2 or len(gets) != 2:
        fail("trace must contain only two searches followed by two gets")
    if not all(entry.get("success") is True for entry in entries):
        fail("every required support operation must succeed")

    matched_searches: list[dict[str, object]] = []
    matched_gets: list[dict[str, object]] = []
    for (name, location), record in zip(TARGETS, records):
        branch_searches = [
            entry for entry in searches
            if entry.get("arguments") == {"name": name, "location": location}
        ]
        if len(branch_searches) != 1:
            fail(f"missing exact name-and-location search for {name}")
        search = branch_searches[0]
        if search.get("result_ids") != [record["id"]]:
            fail(f"search for {name} did not produce exactly its stable ID")
        branch_gets = [
            entry for entry in gets
            if entry.get("arguments") == {"id": record["id"]}
        ]
        if len(branch_gets) != 1:
            fail(f"full-record get for the returned ID of {name} is missing")
        get = branch_gets[0]
        if get.get("record_digest") != digest(record):
            fail(f"full-record evidence for {name} does not match protected data")
        matched_searches.append(search)
        matched_gets.append(get)

    for entry in entries:
        for field in ("started_ns", "finished_ns", "parent_pid"):
            if not isinstance(entry.get(field), int):
                fail(f"audit field {field} is missing or invalid")
        if int(entry["started_ns"]) >= int(entry["finished_ns"]):
            fail("an audited operation has an invalid execution interval")

    if not intervals_overlap(matched_searches):
        fail("the two searches were not executed concurrently")
    search_batches = {entry.get("concurrency_batch") for entry in matched_searches}
    if len(search_batches) != 1 or None in search_batches:
        fail("the two searches did not share one concurrency rendezvous")
    if len({entry["parent_pid"] for entry in matched_searches}) != 1:
        fail("the two searches did not originate in one shell execution")
    if max(int(entry["finished_ns"]) for entry in matched_searches) >= min(
            int(entry["started_ns"]) for entry in matched_gets):
        fail("a get began before both searches had returned")
    if not intervals_overlap(matched_gets):
        fail("the two gets were not executed concurrently")
    get_batches = {entry.get("concurrency_batch") for entry in matched_gets}
    if len(get_batches) != 1 or None in get_batches:
        fail("the two gets did not share one concurrency rendezvous")
    if len({entry["parent_pid"] for entry in matched_gets}) != 1:
        fail("the two gets did not originate in one shell execution")
    if matched_searches[0]["parent_pid"] == matched_gets[0]["parent_pid"]:
        fail("search and get stages were not separate shell executions")


def main() -> int:
    canonical = canonical_connection()
    try:
        records = expected_records(canonical)
        verify_deliverable(records)
        verify_read_only(canonical)
        verify_trace(load_audit(), records)
    finally:
        canonical.close()
    print("PASS: ordered handoff and genuine read-only two-stage parallel trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
