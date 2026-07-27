#!/usr/bin/env python3
"""Protected deterministic verification for the dependent support lookup."""

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
SEED = ROOT / "support_seed.sql"
REPORT = ROOT / "case-report.md"
TARGET_NAME = "Crushed Carton Follow-up"
TARGET_LOCATION = "Acme"
READ_ONLY_TABLES = (
    "cases",
    "saved_profiles",
    "availability",
    "notifications",
    "mutation_log",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    ]


def canonical_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SEED.read_text(encoding="utf-8"))
    connection.row_factory = sqlite3.Row
    return connection


def expected_record(connection: sqlite3.Connection) -> dict[str, object]:
    matches = connection.execute(
        "SELECT id FROM cases WHERE name = ? AND location = ? ORDER BY id",
        (TARGET_NAME, TARGET_LOCATION),
    ).fetchall()
    if len(matches) != 1:
        fail("protected data does not uniquely identify the requested case")
    record = connection.execute(
        "SELECT id, name, location, status, case_date AS date, "
        "priority, owner, summary FROM cases WHERE id = ?",
        (matches[0]["id"],),
    ).fetchone()
    result = {key: record[key] for key in record.keys()}
    if result.get("status") != "active":
        fail("the requested protected case is not active")
    return result


def record_digest(record: dict[str, object]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_report(record: dict[str, object]) -> None:
    if not REPORT.is_file():
        fail("case-report.md is missing")
    expected = (
        "# Case details\n"
        f"- Name: {record['name']}\n"
        f"- Location: {record['location']}\n"
        f"- Stable ID: {record['id']}\n"
        f"- Status: {record['status']}\n"
        f"- Date: {record['date']}\n"
        f"- Priority: {record['priority']}\n"
        f"- Owner: {record['owner']}\n"
        f"- Summary: {record['summary']}\n"
    )
    if REPORT.read_text(encoding="utf-8") != expected:
        fail("case-report.md is not the exact requested target-only full record")


def verify_read_only(canonical_db: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine support executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in READ_ONLY_TABLES:
            if rows(actual, table) != rows(canonical_db, table):
                fail(f"read-only support state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict[str, object]]:
    if not AUDIT.is_file():
        fail("no genuine support executable audit was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"support executable audit is invalid: {error}")
    if len(entries) != 2:
        fail("expected exactly one search followed by one get")

    previous = "0" * 64
    for sequence, entry in enumerate(entries, start=1):
        digest = entry.get("digest")
        unsigned = dict(entry)
        unsigned.pop("digest", None)
        if entry.get("sequence") != sequence or entry.get("previous") != previous:
            fail("support executable audit chain is out of order")
        if digest != hashlib.sha256(canonical(unsigned)).hexdigest():
            fail("support executable audit digest is invalid")
        previous = str(digest)
    return entries


def verify_execution(
    entries: list[dict[str, object]], record: dict[str, object]
) -> None:
    search, get = entries
    if search.get("operation") != "search" or get.get("operation") != "get":
        fail("case-data operations were not one search followed by one get")
    if search.get("success") is not True or get.get("success") is not True:
        fail("both required support operations must succeed")
    if search.get("arguments") != {
        "name": TARGET_NAME,
        "location": TARGET_LOCATION,
    }:
        fail("search did not supply both exact user-provided fields")
    if search.get("result_ids") != [record["id"]]:
        fail("search did not resolve to exactly the requested stable ID")
    if get.get("arguments") != {"id": record["id"]}:
        fail("get did not use the sole stable ID returned by the search")
    if get.get("record_digest") != record_digest(record):
        fail("get evidence does not match the authoritative full record")

    for entry in entries:
        for field in (
            "started_ns",
            "finished_ns",
            "pid",
            "process_started_ticks",
            "parent_pid",
            "parent_started_ticks",
        ):
            if not isinstance(entry.get(field), int):
                fail(f"support audit field {field} is missing or invalid")
        if int(entry["started_ns"]) >= int(entry["finished_ns"]):
            fail("an audited support operation has an invalid execution interval")

    search_process = (
        search["pid"],
        search["process_started_ticks"],
    )
    search_parent = (
        search["parent_pid"],
        search["parent_started_ticks"],
    )
    get_process = (
        get["pid"],
        get["process_started_ticks"],
    )
    get_parent = (
        get["parent_pid"],
        get["parent_started_ticks"],
    )
    if search_parent in (get_parent, get_process):
        fail("search and dependent get were combined in one shell action")
    if search_process == get_process:
        fail("search and dependent get did not use separate command processes")


def main() -> int:
    canonical_db = canonical_connection()
    try:
        record = expected_record(canonical_db)
        verify_report(record)
        verify_read_only(canonical_db)
        verify_execution(load_audit(), record)
    finally:
        canonical_db.close()
    print("PASS: genuine two-stage support lookup and target-only report verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
