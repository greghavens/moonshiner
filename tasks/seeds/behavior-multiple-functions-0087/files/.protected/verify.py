#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-multiple-functions-0087."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "inventory-audit.jsonl"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
DATABASE_PATH = ROOT / ".inventory" / "runtime" / "inventory.sqlite3"
REPORT_PATH = ROOT / "matching-record.md"

EXPECTED_HASHES = {
    "inventoryctl": "f2c48559e4c7c40cafc2fd09e308c2b313ea3f51d1a1462411bad5875f52c16d",
    ".inventory/seed.sql": "62733f9d254a7bd5df2b97fb283273874b5cd89f4d18d6bb62871d4cd5a3c6a4",
    ".protected/audit.key": "7160f940aeadf6c1e9644d0701ffe8bff5b856401a98af336f85be3c5192d7db",
    ".protected/reference_setup.py": "95d11815553670a1efd2e09a71147ea3775ef85ed903ba893e59da5f770d8631",
    "README.md": "c2d9d7d7e141fb1358cfbeb755b7dba9179e9fa2ef175aa9f5a69720dda7a728",
    ".gitignore": "5c1759ddef62fa6f71e8b10ee387519bf1573fbdcea1251bb0fa292c999c13d6",
}
EXPECTED_MODES = {
    "inventoryctl": 0o755,
    ".inventory/seed.sql": 0o644,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
ALLOWED_DIRECTORIES = {
    ".inventory",
    ".inventory/runtime",
    ".protected",
    ".protected/runtime",
}
ALLOWED_FILES = {
    ".gitignore",
    ".inventory/runtime/initialize.lock",
    ".inventory/runtime/inventory.sqlite3",
    ".inventory/seed.sql",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/inventory-audit.jsonl",
    ".protected/verify.py",
    ".reference_solution",
    "README.md",
    "inventoryctl",
    "matching-record.md",
}
EXPECTED_SCHEMA_DIGEST = "77cca3d894b06236e23a00a306047174b6bdf85be7e16d81a667c369a5689c14"
EXPECTED_DATABASE_DIGEST = "5727cff98608c94c56ef7fd0f1917ab501b7b66574e54503ebda1b6fa2f8b9f6"
EXPECTED_MATCH = {
    "id": "inv-187",
    "name": "Packing tape case",
    "location": "Warehouse C",
    "status": "active",
    "date": "2026-11-12",
}
EXPECTED_REPORT = (
    "Packing tape case | ID: inv-187 | location: Warehouse C | "
    "status: active | date: 2026-11-12\n"
)


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
            if relative not in ALLOWED_DIRECTORIES:
                fail(f"unexpected scratch directory remains: {relative}")
        elif path.is_file():
            if relative not in ALLOWED_FILES:
                fail(f"unexpected scratch file remains: {relative}")
        else:
            fail(f"unexpected scratch artifact remains: {relative}")


def verify_protected_inputs() -> None:
    for relative, expected_hash in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected_hash:
            fail(f"protected inventory input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected inventory input mode changed: {relative}")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("matching-record.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("matching-record.md is not the exact grounded matching record")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite inventory registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, location, status, item_date, lifecycle,
                       sku, quantity, owner, notes
                FROM item_records ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
            preferences = connection.execute(
                """
                SELECT profile_id, default_location, notification_channel
                FROM preferences ORDER BY profile_id
                """
            ).fetchall()
            schema = connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_schema ORDER BY type, name
                """
            ).fetchall()
            sequence = connection.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"inventory registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("inventory registry database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("inventory registry schema changed")
    snapshot = {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
        "preferences": [list(row) for row in preferences],
        "sqlite_sequence": [list(row) for row in sequence],
    }
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail("an inventory record, preference, or notification changed")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine inventoryctl execution evidence was produced")
    audit_key = AUDIT_KEY_PATH.read_bytes().rstrip(b"\n")
    events: list[dict] = []
    for number, raw_line in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            signed_event = json.loads(raw_line)
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
            fail(f"audit event {number} was not emitted intact by inventoryctl")
        for field in ("start_ns", "end_ns", "process_pid", "parent_pid"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        for field in ("process_start_ticks", "parent_start_ticks"):
            if not isinstance(event.get(field), str) or not event[field]:
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        if not isinstance(event.get("event_id"), str) or not event["event_id"]:
            fail(f"audit event {number} has no event ID")
        events.append(event)
    if len({event["event_id"] for event in events}) != len(events):
        fail("the inventory execution evidence contains duplicate event IDs")
    return sorted(events, key=lambda event: event["start_ns"])


def verify_execution(events: list[dict]) -> None:
    if len(events) != 2:
        fail("expected exactly one help invocation and one inventory operation")
    help_event, search_event = events
    if help_event.get("operation") != "help" or help_event.get("success") is not True:
        fail("the executable's built-in help was not consulted first")
    if search_event.get("operation") != "search":
        fail("the matching-record search operation was not selected")
    if search_event.get("success") is not True:
        fail("the exact matching-record search did not succeed")
    if search_event.get("name") != "Packing tape case":
        fail("the search did not use the exact requested item name")
    if search_event.get("location") != "Warehouse C":
        fail("the search did not use the exact requested warehouse")
    if search_event.get("result_count") != 1:
        fail("the exact scoped search did not resolve to one match")
    if search_event.get("result_ids") != ["inv-187"]:
        fail("the search result was not the unique scoped record")
    expected_digest = hashlib.sha256(canonical([EXPECTED_MATCH])).hexdigest()
    if search_event.get("result_sha256") != expected_digest:
        fail("the reported match was not grounded in the search result")
    if search_event.get("returned_fields") != sorted(EXPECTED_MATCH):
        fail("the search did not return every required public record field")
    help_process = (
        help_event.get("process_pid"),
        help_event.get("process_start_ticks"),
    )
    search_process = (
        search_event.get("process_pid"),
        search_event.get("process_start_ticks"),
    )
    if help_process == search_process:
        fail("help and search were not separate executable invocations")
    help_parent = (
        help_event.get("parent_pid"),
        help_event.get("parent_start_ticks"),
    )
    search_parent = (
        search_event.get("parent_pid"),
        search_event.get("parent_start_ticks"),
    )
    if help_parent == search_parent:
        fail("help and search were not run in separate Bash interactions")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_report()
    verify_database_state()
    verify_execution(load_events())
    print(
        "PASS: genuine Pi Bash execution selected one exact matching-record "
        "search and preserved all inventory and notification state"
    )


if __name__ == "__main__":
    main()
