#!/usr/bin/env python3
"""Protected verification for parallel checks and selective error recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".expense-runtime"
DATABASE = RUNTIME / "expenses.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "availability-report.md"
SEED = PROTECTED / "expense_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "expense-availability"
DOCUMENTATION = ROOT / "EXPENSES.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "expense": "Chicago rail fare — outreach trip",
        "city": "Chicago",
        "date": "2026-08-03",
    },
    {
        "expense": "Boston team lunch — budget workshop",
        "city": "Boston",
        "date": "2026-08-03",
    },
)
DATA_TABLES = ("expense_items", "availability", "notifications")
EXPECTED_CLIENT_SHA256 = "da68071156e350ee66f9e9199fcdd2d9e2456d756b8fd021a02c3981b30b4757"
EXPECTED_SEED_SHA256 = "1c17e1246f030df12bfa500f1257e1a2b4a85314f2c6b99904c010a06857ad93"
EXPECTED_KEY_SHA256 = "b21115aaca43f9d52d90b27c56523c0379c05455d71652e56abf631b5fbe47a1"
EXPECTED_DOCUMENTATION_SHA256 = "0749940b2f3bb4b7ff293a1df8b7a8a341aa4fdbea383af828788878e95bdfb2"
EXPECTED_SETUP_SHA256 = "e1d38fcf5e60e6e4df5df2cee107a4fd6c4f5c400e38bf7241406c5e53883a37"
EXPECTED_GITIGNORE_SHA256 = "b356e2601f8673df386a8160ebeb6ce7fd7a9fadfc9adab0172b1a52427aa0d4"
ALLOWED_ROOT_ENTRIES = {
    ".expense-runtime",
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution.py",
    "EXPENSES.md",
    "availability-report.md",
    "expense-availability",
}
EXPECTED_PROTECTED_ENTRIES = {
    "audit.key",
    "expense_seed.sql",
    "setup.py",
    "verify.py",
}
EXPECTED_RUNTIME_ENTRIES = {
    "attempts.json",
    "attempts.lock",
    "audit.jsonl",
    "expenses.sqlite3",
    "initialize.lock",
}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected sandbox input {path.name}: {error}")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def canonical_connection() -> sqlite3.Connection:
    protected_files = (
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine expense executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected expense seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected signing material"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "expense documentation"),
        (SETUP, EXPECTED_SETUP_SHA256, "protected reference setup"),
        (GITIGNORE, EXPECTED_GITIGNORE_SHA256, "sandbox ignore rules"),
    )
    for path, expected, label in protected_files:
        if not path.is_file() or sha256(path) != expected:
            fail(f"{label} changed")
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SEED.read_text(encoding="utf-8"))
    except (OSError, sqlite3.Error) as error:
        fail(f"cannot load protected expense seed: {error}")
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[tuple[Any, ...]]:
    return sorted(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))


def schema(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    ]


def expected_results(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    results: list[dict[str, Any]] = []
    for target in TARGETS:
        row = connection.execute(
            "SELECT expense_name AS expense, city, availability_date AS date, "
            "available FROM availability WHERE expense_name = ? AND city = ? "
            "AND availability_date = ?",
            (target["expense"], target["city"], target["date"]),
        ).fetchone()
        if row is None:
            fail("the protected seed lacks a requested availability record")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_deliverable(results: list[dict[str, Any]]) -> None:
    if not DELIVERABLE.is_file():
        fail("availability-report.md is missing")
    lines = []
    for result in results:
        availability_text = "available" if result["available"] else "unavailable"
        lines.append(
            f"- {result['expense']} in {result['city']}: {availability_text} "
            f"on {result['date']}."
        )
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read availability-report.md as UTF-8: {error}")
    if actual != expected:
        fail("availability-report.md does not contain the exact grounded two-line report")
    if len(actual.splitlines()) != 2 or any(
        not line.startswith("- ") for line in actual.splitlines()
    ):
        fail("availability-report.md is not exactly two Markdown bullets")
    forbidden = ("expense_service_busy", "operation_failed", "retryable", "transient")
    if any(value in actual for value in forbidden):
        fail("availability-report.md exposes internal recovery details")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine expense executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            fail("the expense database failed its integrity check")
        if schema(actual) != schema(canonical):
            fail("the expense database schema changed")
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only expense state changed in table {table}")
    finally:
        actual.close()


def verify_layout() -> None:
    root_entries = {path.name for path in ROOT.iterdir()}
    unexpected = sorted(root_entries - ALLOWED_ROOT_ENTRIES)
    if unexpected:
        fail(f"unexpected scratch artifact remains: {unexpected[0]}")
    if {path.name for path in PROTECTED.iterdir()} != EXPECTED_PROTECTED_ENTRIES:
        fail("the protected file inventory changed")
    if not RUNTIME.is_dir():
        fail("the genuine expense runtime directory is missing")
    if {path.name for path in RUNTIME.iterdir()} != EXPECTED_RUNTIME_ENTRIES:
        fail("the expense runtime has missing or unexpected artifacts")


def load_audit() -> list[dict[str, Any]]:
    if not AUDIT.is_file():
        fail("no signed expense-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"expense operation evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one selective retry")
    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    verified: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail("an expense operation evidence entry is not an object")
        unsigned = dict(entry)
        signature = unsigned.pop("signature", None)
        expected = hmac.new(key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("expense operation evidence was not emitted intact by the executable")
        verified.append(unsigned)
    return verified


def intervals_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a create, cancel, update, notify, or other forbidden operation was used")
    for entry in entries:
        for field in (
            "attempt",
            "started_ns",
            "finished_ns",
            "pid",
            "parent_pid",
        ):
            if not isinstance(entry.get(field), int) or isinstance(entry.get(field), bool):
                fail(f"signed execution field {field} is missing or invalid")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an availability operation has an invalid execution interval")

    branches = []
    for target in TARGETS:
        branch = [entry for entry in entries if entry.get("arguments") == target]
        branches.append(sorted(branch, key=lambda value: value["started_ns"]))
    if len(branches[0]) != 1 or len(branches[1]) != 2:
        fail("the successful branch was repeated or the failed branch was not retried once")

    retained_success = branches[0][0]
    initial_failure, retry = branches[1]
    if retained_success.get("success") is not True or retained_success["attempt"] != 1:
        fail("the independent successful branch does not have one valid first response")
    if retained_success.get("result_digest") != digest(results[0]):
        fail("the retained successful availability response is incorrect")

    if initial_failure.get("success") is not False or initial_failure["attempt"] != 1:
        fail("the retry branch does not begin with exactly one failed check")
    if (
        initial_failure.get("error_code") != "expense_service_busy"
        or initial_failure.get("retryable") is not True
        or initial_failure.get("transient") is not True
    ):
        fail("the failed initial branch was not explicitly transient and retryable")

    if retry.get("success") is not True or retry["attempt"] != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != initial_failure.get("arguments"):
        fail("the retry changed an expense, city, or date input")
    if retry.get("result_digest") != digest(results[1]):
        fail("the retry's successful availability response is incorrect")

    if retained_success["pid"] == initial_failure["pid"]:
        fail("the two initial checks were not distinct executable processes")
    if not intervals_overlap(retained_success, initial_failure):
        fail("the two independent initial checks were not issued together")
    if retry["started_ns"] <= max(
        retained_success["finished_ns"], initial_failure["finished_ns"]
    ):
        fail("the retry began before both initial branches returned")


def main() -> int:
    canonical = canonical_connection()
    try:
        results = expected_results(canonical)
        verify_deliverable(results)
        verify_read_only(canonical)
        verify_trace(load_audit(), results)
        verify_layout()
    finally:
        canonical.close()
    print(
        "verification passed: genuine parallel checks, retained success, "
        "one selective retry, and read-only report verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
