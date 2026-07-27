#!/usr/bin/env python3
"""Protected deterministic verifier for the exact order-resolution task."""

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
REPORT = ROOT / "order-resolution.md"
SEED = ROOT / "order_seed.sql"
CLIENT = ROOT / "order_register.py"
TARGET_NAME = "Ergonomic Chair Order"
TARGET_LOCATION = "Boise"
REQUIRED_STATUS = "active"
TABLES = (
    "orders",
    "profiles",
    "availability",
    "notifications",
    "mutation_log",
)
EXPECTED_SEED_SHA256 = "88206956e77f4dbef80e5cccf3d5ab68ed4060ba95b919661b1d8b0766df5b57"
EXPECTED_CLIENT_SHA256 = "56bbcd5f6d6516e1d83660e3bb67d7922546bef9c78c9aacc7083f0decb9959a"


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


def expected_record(connection: sqlite3.Connection) -> dict:
    connection.row_factory = sqlite3.Row
    matches = connection.execute(
        "SELECT id FROM orders WHERE name = ? AND location = ? ORDER BY id",
        (TARGET_NAME, TARGET_LOCATION),
    ).fetchall()
    if len(matches) != 1:
        fail("protected register does not contain one exact target")
    row = connection.execute(
        "SELECT id, name, location, status, order_date, account, item_count, "
        "total_cents FROM orders WHERE id = ?",
        (matches[0]["id"],),
    ).fetchone()
    record = dict(row)
    if record["status"] != REQUIRED_STATUS:
        fail("protected exact target does not have the required active status")
    return record


def verify_report(record: dict) -> None:
    if not REPORT.is_file():
        fail("order-resolution.md is missing")
    expected = "\n".join(
        (
            f"- Stable ID: {record['id']}",
            f"- Status: {record['status']}",
            f"- Location/date: {record['location']} / {record['order_date']}",
        )
    )
    actual = REPORT.read_text(encoding="utf-8")
    if actual not in {expected, expected + "\n"}:
        fail("order-resolution.md does not contain exactly the required three bullets")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine order-register executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine order-register audit was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"order-register audit is invalid: {error}")
    if len(entries) != 2:
        fail("expected exactly one search and one get, with no other operations")
    return entries


def digest(record: dict) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def verify_trace(entries: list[dict], record: dict) -> None:
    search, get = entries
    if search.get("operation") != "search" or get.get("operation") != "get":
        fail("operations must be one search followed by one dependent get")
    if search.get("arguments") != {
        "name": TARGET_NAME,
        "location": TARGET_LOCATION,
    }:
        fail("search did not use the exact supplied name and location together")
    if search.get("result_ids") != [record["id"]]:
        fail("search did not resolve exactly the protected stable ID")
    if get.get("arguments") != {"id": record["id"]}:
        fail("get did not use only the stable ID returned by search")
    if get.get("record_digest") != digest(record):
        fail("full-record evidence does not match the stored target")
    if not all(entry.get("success") is True for entry in entries):
        fail("both required order operations must succeed")

    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int):
                fail(f"audit process/timing field {field} is missing")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an audited order operation has an invalid interval")
    if search["finished_ns"] >= get["started_ns"]:
        fail("full-record retrieval began before search returned its stable ID")


def main() -> int:
    canonical = canonical_connection()
    try:
        record = expected_record(canonical)
        verify_report(record)
        verify_read_only(canonical)
        verify_trace(load_audit(), record)
    finally:
        canonical.close()
    print("PASS: exact three-bullet resolution and genuine dependent trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
