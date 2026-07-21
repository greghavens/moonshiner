#!/usr/bin/env python3
"""Protected deterministic verifier for the procurement dependency task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".order-runtime"
DATABASE = RUNTIME / "orders.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
BRIEF = ROOT / "fulfillment-brief.md"
SEED = ROOT / "order_seed.sql"
CLIENT = ROOT / "order_desk.py"
TARGETS = (
    ("Ergonomic Chair Order", "Boise Office"),
    ("New-Hire Welcome Kit Order", "Phoenix Branch"),
)
TABLES = (
    "purchase_orders",
    "preferences",
    "availability",
    "notifications",
    "mutation_log",
)
EXPECTED_SEED_SHA256 = "082196612ec8721295a3ded1efb30429c8c0f23ce5ce041e18b83dc804df7911"
EXPECTED_CLIENT_SHA256 = "f25b62be61f32ed5ac0c5729665a25bcdc2c5e708e92db90b84866a006c232b3"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_connection() -> sqlite3.Connection:
    if sha256(SEED) != EXPECTED_SEED_SHA256:
        fail("protected order seed changed")
    if sha256(CLIENT) != EXPECTED_CLIENT_SHA256:
        fail("genuine order executable changed")
    connection = sqlite3.connect(":memory:")
    connection.executescript(SEED.read_text(encoding="utf-8"))
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    ]


def expected_records(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    records = []
    for name, location in TARGETS:
        matches = connection.execute(
            "SELECT id FROM purchase_orders WHERE name = ? AND location = ? ORDER BY id",
            (name, location),
        ).fetchall()
        if len(matches) != 1:
            fail("protected order state no longer gives one stable ID per target")
        row = connection.execute(
            "SELECT id, name, location, status, requested_for, vendor, item_count, "
            "total_cents FROM purchase_orders WHERE id = ?",
            (matches[0]["id"],),
        ).fetchone()
        records.append(dict(row))
    return records


def verify_brief(records: list[dict]) -> None:
    if not BRIEF.is_file():
        fail("fulfillment-brief.md is missing")
    first, second = records
    lines = [
        f"- {first['name']} at {first['location']} has status {first['status']}.",
        f"- {second['name']} at {second['location']} has status {second['status']}.",
    ]
    if first["status"] == second["status"]:
        lines[1] += f" Statuses match: {first['status']}."
    else:
        lines[1] += (
            f" Statuses differ: {first['status']} versus {second['status']}."
        )
    expected_without_final_newline = "\n".join(lines)
    actual = BRIEF.read_text(encoding="utf-8")
    if actual not in {
        expected_without_final_newline,
        expected_without_final_newline + "\n",
    }:
        fail("fulfillment-brief.md does not have the exact two-bullet content required")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine order executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine order executable audit was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"order executable audit is invalid: {error}")
    if len(entries) != 4:
        fail("expected exactly two searches and two gets, with no other order operations")
    return entries


def intervals_overlap(entries: list[dict]) -> bool:
    return max(entry["started_ns"] for entry in entries) < min(
        entry["finished_ns"] for entry in entries
    )


def digest(record: dict) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def verify_trace(entries: list[dict], records: list[dict]) -> None:
    searches = [entry for entry in entries if entry.get("operation") == "search"]
    gets = [entry for entry in entries if entry.get("operation") == "get"]
    if len(searches) != 2 or len(gets) != 2:
        fail("trace must contain only two searches and two full-record gets")
    if not all(entry.get("success") is True for entry in entries):
        fail("every required order operation must succeed")

    matched_searches = []
    matched_gets = []
    for (name, location), record in zip(TARGETS, records, strict=True):
        branch_searches = [
            entry
            for entry in searches
            if entry.get("arguments") == {"name": name, "location": location}
        ]
        if len(branch_searches) != 1:
            fail(f"missing exact name-and-location search for {name}")
        search = branch_searches[0]
        if search.get("result_ids") != [record["id"]]:
            fail(f"search for {name} did not resolve to exactly one stable ID")

        branch_gets = [
            entry
            for entry in gets
            if entry.get("arguments") == {"id": record["id"]}
        ]
        if len(branch_gets) != 1:
            fail(f"full get for the returned ID of {name} is missing")
        get = branch_gets[0]
        if get.get("record_digest") != digest(record):
            fail(f"full-record evidence for {name} does not match stored data")
        matched_searches.append(search)
        matched_gets.append(get)

    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int):
                fail(f"audit process/timing field {field} is missing")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an audited order operation has an invalid interval")

    if not intervals_overlap(matched_searches):
        fail("the two searches were not executed concurrently")
    if len({entry["parent_pid"] for entry in matched_searches}) != 1:
        fail("the two searches did not originate in one shell execution")
    if len({entry["pid"] for entry in matched_searches}) != 2:
        fail("the two searches were not separate executable processes")

    if max(entry["finished_ns"] for entry in matched_searches) >= min(
        entry["started_ns"] for entry in matched_gets
    ):
        fail("a get began before both search results had returned")
    if not intervals_overlap(matched_gets):
        fail("the two gets were not executed concurrently")
    if len({entry["parent_pid"] for entry in matched_gets}) != 1:
        fail("the two gets did not originate in one shell execution")
    if len({entry["pid"] for entry in matched_gets}) != 2:
        fail("the two gets were not separate executable processes")
    if matched_searches[0]["parent_pid"] == matched_gets[0]["parent_pid"]:
        fail("search and get stages were not separate shell executions")


def main() -> int:
    canonical = canonical_connection()
    try:
        records = expected_records(canonical)
        verify_brief(records)
        verify_read_only(canonical)
        verify_trace(load_audit(), records)
    finally:
        canonical.close()
    print("PASS: exact brief and genuine read-only two-stage parallel trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
