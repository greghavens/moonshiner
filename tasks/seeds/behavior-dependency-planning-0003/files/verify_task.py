#!/usr/bin/env python3
"""Protected deterministic verifier for the lodging dependency task."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".lodging-runtime"
DATABASE = RUNTIME / "lodging.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "lodging-audit.md"
TARGETS = (
    ("Orchard Room Leadership Retreat", "Austin"),
    ("Juniper Suite Planning Stay", "Raleigh"),
)
TABLES = (
    "reservations", "saved_preferences", "availability",
    "notifications", "mutation_log",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return [tuple(row) for row in
            connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]


def canonical_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript((ROOT / "lodging_seed.sql").read_text(encoding="utf-8"))
    return connection


def expected_records(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    records = []
    for name, location in TARGETS:
        matches = connection.execute(
            "SELECT id FROM reservations WHERE name = ? AND location = ? ORDER BY id",
            (name, location),
        ).fetchall()
        if len(matches) != 1:
            fail("protected data no longer gives one stable ID per requested branch")
        record = connection.execute(
            "SELECT id, name, location, stay_date AS date, status "
            "FROM reservations WHERE id = ?",
            (matches[0]["id"],),
        ).fetchone()
        records.append(dict(record))
    return records


def verify_deliverable(records: list[dict]) -> None:
    if not DELIVERABLE.is_file():
        fail("lodging-audit.md is missing")
    lines = ["| Record | ID | Date | Status |", "|---|---|---|---|"]
    lines.extend(
        f"| {record['name']} | {record['id']} | {record['date']} | {record['status']} |"
        for record in records
    )
    expected = "\n".join(lines) + "\n"
    if DELIVERABLE.read_text(encoding="utf-8") != expected:
        fail("lodging-audit.md is not the exact required two-row table")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine lodging executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine lodging executable audit was recorded")
    try:
        entries = [json.loads(line) for line in AUDIT.read_text(
            encoding="utf-8").splitlines()]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"lodging executable audit is invalid: {error}")
    if len(entries) != 4:
        fail("expected exactly two searches and two gets, with no other operations")
    return entries


def intervals_overlap(entries: list[dict]) -> bool:
    return max(entry["started_ns"] for entry in entries) < min(
        entry["finished_ns"] for entry in entries
    )


def verify_trace(entries: list[dict], records: list[dict]) -> None:
    searches = [entry for entry in entries if entry.get("operation") == "search"]
    gets = [entry for entry in entries if entry.get("operation") == "get"]
    if len(searches) != 2 or len(gets) != 2:
        fail("trace must contain only two searches followed by two gets")
    if not all(entry.get("success") is True for entry in entries):
        fail("every required lodging operation must succeed")

    matched_searches = []
    matched_gets = []
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
        matched_searches.append(search)
        matched_gets.append(branch_gets[0])

    for entry in entries:
        for field in ("started_ns", "finished_ns", "parent_pid"):
            if not isinstance(entry.get(field), int):
                fail(f"audit field {field} is missing or invalid")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an audited operation has an invalid execution interval")

    if not intervals_overlap(matched_searches):
        fail("the two searches were not executed concurrently")
    if len({entry["parent_pid"] for entry in matched_searches}) != 1:
        fail("the two searches did not originate in one shell execution")
    if max(entry["finished_ns"] for entry in matched_searches) >= min(
            entry["started_ns"] for entry in matched_gets):
        fail("a get began before both searches had returned")
    if not intervals_overlap(matched_gets):
        fail("the two gets were not executed concurrently")
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
    print("PASS: exact table and genuine read-only two-stage parallel trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
