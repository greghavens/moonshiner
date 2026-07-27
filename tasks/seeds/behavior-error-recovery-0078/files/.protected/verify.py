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
RUNTIME = ROOT / ".subscription-runtime"
DATABASE = RUNTIME / "subscriptions.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "availability-report.md"
SEED = PROTECTED / "subscription_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "subscription-availability"
DOCUMENTATION = ROOT / "SUBSCRIPTIONS.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "subscription": "Family fiber subscription",
        "account": "Family Account",
        "date": "2026-08-03",
    },
    {
        "subscription": "Studio tablet data plan",
        "account": "Studio Account",
        "date": "2026-08-03",
    },
)
DATA_TABLES = ("subscription_items", "availability", "notifications")
EXPECTED_CLIENT_SHA256 = "d0d0dcca7daf1a271e01a8e365a83effe67cc7560fb45fc4545c7104ad7d53d6"
EXPECTED_SEED_SHA256 = "afc993be94c476d86aaef707e5d5ba396a1cbcc90b7bcc18467237c584c3d659"
EXPECTED_KEY_SHA256 = "2c899f5ce5db47ae141c0f5301d3e1a67a0b02d71ad06be975c816ff2984a59f"
EXPECTED_DOCUMENTATION_SHA256 = "bb9b643144c83f0b6bf3c6eaf1687f5b80c2b0055482a2bd185758898b320a78"
EXPECTED_SETUP_SHA256 = "55bdf83913b789b8066bea6c431cf1c22b2169aaffc756bef80f8ca8539eb20d"
EXPECTED_GITIGNORE_SHA256 = "46a620a5c82be066f5d360645d04b0cdc860a025682083864cedfca0e60f354f"
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution.py",
    ".subscription-runtime",
    "SUBSCRIPTIONS.md",
    "availability-report.md",
    "subscription-availability",
}
EXPECTED_PROTECTED_ENTRIES = {
    "audit.key",
    "setup.py",
    "subscription_seed.sql",
    "verify.py",
}
EXPECTED_RUNTIME_ENTRIES = {
    "attempts.json",
    "attempts.lock",
    "audit.jsonl",
    "initialize.lock",
    "subscriptions.sqlite3",
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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine subscription executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected subscription seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected signing material"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "subscription documentation"),
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
        fail(f"cannot load protected subscription seed: {error}")
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
            "SELECT subscription_name AS subscription, account, "
            "availability_date AS date, available FROM availability "
            "WHERE subscription_name = ? AND account = ? AND availability_date = ?",
            (target["subscription"], target["account"], target["date"]),
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
        lines.append(f"- {result['subscription']}: {availability_text}")
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read availability-report.md as UTF-8: {error}")
    if actual != expected:
        fail("availability-report.md is not the exact grounded two-value report")
    if len(actual.splitlines()) != 2 or any(
        not line.startswith("- ") for line in actual.splitlines()
    ):
        fail("availability-report.md is not exactly two Markdown bullets")
    forbidden = (
        "Family Account",
        "Studio Account",
        "2026-08-03",
        "subscription_service_busy",
        "operation_failed",
        "retryable",
        "transient",
        "retry",
    )
    if any(value in actual for value in forbidden):
        fail("availability-report.md contains information beyond availability values")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine subscription executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            fail("the subscription database failed its integrity check")
        if schema(actual) != schema(canonical):
            fail("the subscription database schema changed")
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only subscription state changed in table {table}")
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
        fail("the genuine subscription runtime directory is missing")
    if {path.name for path in RUNTIME.iterdir()} != EXPECTED_RUNTIME_ENTRIES:
        fail("the subscription runtime has missing or unexpected artifacts")


def load_audit() -> list[dict[str, Any]]:
    if not AUDIT.is_file():
        fail("no signed subscription-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"subscription operation evidence is invalid: {error}")
    if not entries:
        fail("no signed subscription-executable evidence was recorded")
    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    verified: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail("a subscription operation evidence entry is not an object")
        unsigned = dict(entry)
        signature = unsigned.pop("signature", None)
        expected = hmac.new(key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("subscription operation evidence was not emitted intact")
        verified.append(unsigned)
    return verified


def intervals_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    help_entries = [entry for entry in entries if entry.get("event") == "help"]
    operation_entries = [
        entry for entry in entries if isinstance(entry.get("operation"), str)
    ]
    if not help_entries or entries[0].get("event") != "help":
        fail("the executable's live help was not consulted before data operations")
    if len(operation_entries) != 3:
        fail("expected exactly two initial checks and one selective retry")
    if len(help_entries) + len(operation_entries) != len(entries):
        fail("unexpected signed executable evidence was recorded")

    for entry in help_entries:
        arguments = entry.get("arguments")
        if not isinstance(arguments, list) or not any(
            argument in ("-h", "--help") for argument in arguments
        ):
            fail("a recorded help invocation lacks a built-in help flag")
        for field in ("started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int) or isinstance(
                entry.get(field), bool
            ):
                fail(f"signed help field {field} is missing or invalid")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("a help invocation has an invalid execution interval")
    if help_entries[0]["finished_ns"] >= min(
        entry["started_ns"] for entry in operation_entries
    ):
        fail("the first data operation began before live help completed")

    entries = operation_entries
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a create, cancel, update, notify, or other forbidden operation was used")
    for entry in entries:
        for field in ("attempt", "started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int) or isinstance(
                entry.get(field), bool
            ):
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
        fail("the retained branch lacks one valid initial success")
    if retained_success.get("result_digest") != digest(results[0]):
        fail("the retained successful availability response is incorrect")

    if initial_failure.get("success") is not False or initial_failure["attempt"] != 1:
        fail("the retry branch does not begin with exactly one failed check")
    if (
        initial_failure.get("error_code") != "subscription_service_busy"
        or initial_failure.get("retryable") is not True
        or initial_failure.get("transient") is not True
    ):
        fail("the failed initial branch was not explicitly transient and retryable")

    if retry.get("success") is not True or retry["attempt"] != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != initial_failure.get("arguments"):
        fail("the retry changed a subscription, account, or date input")
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
        "verification passed: live help, genuine paired checks, retained success, "
        "one selective retry, and grounded read-only report verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
