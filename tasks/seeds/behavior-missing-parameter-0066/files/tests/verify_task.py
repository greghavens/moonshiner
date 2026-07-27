#!/usr/bin/env python3
"""Protected deterministic verifier for the saved-location commerce task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".commerce-runtime"
DATABASE = RUNTIME / "commerce.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
REPORT = ROOT / "active-orders.md"
SEED = ROOT / "commerce_seed.sql"
CLIENT = ROOT / "commerce_desk.py"
TABLES = ("orders", "profile_preferences", "mutation_log")
EXPECTED_SEED_SHA256 = "8447ad23f8327c5f913745872c57c0e172396ef3c97fb73ce038871806a980be"
EXPECTED_CLIENT_SHA256 = "ef51e34b59aa365f27921895d0f16f070f98dbf74fd943e599c19d4a7a904b70"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_connection() -> sqlite3.Connection:
    if sha256(SEED) != EXPECTED_SEED_SHA256:
        fail("protected commerce seed changed")
    if sha256(CLIENT) != EXPECTED_CLIENT_SHA256:
        fail("genuine commerce executable changed")
    connection = sqlite3.connect(":memory:")
    connection.executescript(SEED.read_text(encoding="utf-8"))
    connection.row_factory = sqlite3.Row
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    ]


def expected_evidence(connection: sqlite3.Connection) -> tuple[dict, dict]:
    profile_rows = connection.execute(
        "SELECT preference_key, preference_value "
        "FROM profile_preferences ORDER BY preference_key"
    ).fetchall()
    profile = {
        row["preference_key"]: row["preference_value"] for row in profile_rows
    }
    location = profile.get("preferred_fulfillment_location")
    if not isinstance(location, str) or not location:
        fail("protected profile has no saved fulfillment location")
    order_rows = connection.execute(
        "SELECT id, name, location, status FROM orders "
        "WHERE location = ? AND status = ? ORDER BY id",
        (location, "active"),
    ).fetchall()
    listing = {
        "count": len(order_rows),
        "orders": [dict(row) for row in order_rows],
    }
    if not listing["orders"]:
        fail("protected state has no active orders at the saved location")
    return profile, listing


def digest(result: dict) -> str:
    payload = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def verify_report(profile: dict, listing: dict) -> None:
    if not REPORT.is_file():
        fail("active-orders.md is missing")
    location = profile["preferred_fulfillment_location"]
    lines = [f"Saved fulfillment location: {location}"]
    lines.extend(f"- {order['name']} — {order['id']}" for order in listing["orders"])
    expected = "\n".join(lines)
    actual = REPORT.read_text(encoding="utf-8")
    if actual not in {expected, expected + "\n"}:
        fail("active-orders.md does not contain the exact grounded location and list")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine commerce executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only commerce state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine commerce executable audit was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"commerce executable audit is invalid: {error}")
    if len(entries) != 2:
        fail("expected exactly one profile read and one active-order listing")
    return entries


def verify_trace(entries: list[dict], profile: dict, listing: dict) -> None:
    profile_entry, list_entry = entries
    if profile_entry.get("operation") != "profile":
        fail("the first commerce operation must retrieve the saved profile")
    if profile_entry.get("arguments") != {}:
        fail("the saved-profile operation received unexpected arguments")
    if profile_entry.get("success") is not True:
        fail("the saved-profile operation did not succeed")
    if profile_entry.get("result_digest") != digest(profile):
        fail("the saved-profile evidence does not match stored data")

    location = profile["preferred_fulfillment_location"]
    if list_entry.get("operation") != "list":
        fail("the second commerce operation must be the requested order listing")
    if list_entry.get("arguments") != {"location": location, "status": "active"}:
        fail("the listing was not grounded in the saved location and active status")
    if list_entry.get("success") is not True:
        fail("the active-order listing did not succeed")
    if list_entry.get("result_digest") != digest(listing):
        fail("the active-order listing evidence does not match stored data")
    if list_entry.get("result_ids") != [
        order["id"] for order in listing["orders"]
    ]:
        fail("the active-order listing did not return the expected stable IDs")

    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int):
                fail(f"audit process/timing field {field} is missing")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an audited commerce operation has an invalid interval")
    if profile_entry["finished_ns"] >= list_entry["started_ns"]:
        fail("the active-order listing began before the saved profile returned")


def main() -> int:
    canonical = canonical_connection()
    try:
        profile, listing = expected_evidence(canonical)
        verify_report(profile, listing)
        verify_read_only(canonical)
        verify_trace(load_audit(), profile, listing)
    finally:
        canonical.close()
    print("PASS: grounded active orders and genuine read-only commerce trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
