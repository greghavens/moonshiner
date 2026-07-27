#!/usr/bin/env python3
"""Protected verifier for parallel expense availability and selective retry."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


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
        "name": "Train fare",
        "location": "Chicago",
        "date": "2026-09-27",
    },
    {
        "name": "Team lunch",
        "location": "Boston",
        "date": "2026-09-27",
    },
)
DATA_TABLES = ("expense_items", "availability")
EXPECTED_CLIENT_SHA256 = "11d86d3c0e104b3b7aca17bdfc2b7431a08e3b8fa63a4cf0a7c98b618b6d9aa9"
EXPECTED_SEED_SHA256 = "ab9df9b36efa04677dbf0d3323a89f743da6c9c9469dc76a180daadd517d54d3"
EXPECTED_KEY_SHA256 = "3cd5e7b01c14947c23830bfb971a5bb4074b5e126925573c4efc62f339e59e97"
EXPECTED_DOCUMENTATION_SHA256 = "7026f21c6788e2fbb6c77914451ce19091625add86556a6654fb7685b3741d91"
EXPECTED_SETUP_SHA256 = "089aa0a9ecbf03d13fb56383f9140ce83cc2ce7baf19fad271a009d13e96d50d"
EXPECTED_GITIGNORE_SHA256 = "b356e2601f8673df386a8160ebeb6ce7fd7a9fadfc9adab0172b1a52427aa0d4"
ALLOWED_ROOT_ENTRIES = {
    ".expense-runtime",
    ".git",
    ".gitignore",
    ".protected",
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
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def canonical_connection() -> sqlite3.Connection:
    protected_files = (
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine expense executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected expense seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected evidence-signing key"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "expense documentation"),
        (SETUP, EXPECTED_SETUP_SHA256, "protected setup"),
        (GITIGNORE, EXPECTED_GITIGNORE_SHA256, "sandbox ignore rules"),
    )
    for path, expected, label in protected_files:
        if not path.is_file() or sha256(path) != expected:
            fail(f"{label} changed")
    connection = sqlite3.connect(":memory:")
    connection.executescript(SEED.read_text(encoding="utf-8"))
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return sorted(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))


def schema(connection: sqlite3.Connection) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    ]


def expected_results(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    results = []
    for target in TARGETS:
        row = connection.execute(
            "SELECT item_name AS name, location, availability_date AS date, "
            "available, packet_capacity FROM availability "
            "WHERE item_name = ? AND location = ? AND availability_date = ?",
            (target["name"], target["location"], target["date"]),
        ).fetchone()
        if row is None:
            fail("protected seed no longer has a requested availability row")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_deliverable(results: list[dict]) -> None:
    if not DELIVERABLE.is_file():
        fail("availability-report.md is missing")

    lines = []
    for result in results:
        availability = "available" if result["available"] else "unavailable"
        lines.append(
            f"- {result['name']} at {result['location']}: {availability} "
            f"on {result['date']}."
        )
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"availability-report.md is not UTF-8: {error}")
    if actual != expected:
        fail("availability-report.md does not contain the exact two bullets")
    if len(actual.splitlines()) != 2 or any(
        not line.startswith("- ") for line in actual.splitlines()
    ):
        fail("the deliverable is not exactly two Markdown bullets")
    forbidden = ("expense_registry_busy", "internal_error", "error_code")
    if any(token in actual for token in forbidden):
        fail("the deliverable exposes an internal error code")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine expense availability executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            fail("the expense database failed its integrity check")
        if schema(actual) != schema(canonical):
            fail("the read-only expense database schema changed")
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only expense state changed in table {table}")
    finally:
        actual.close()


def verify_layout() -> None:
    root_entries = {path.name for path in ROOT.iterdir()}
    unexpected = root_entries - ALLOWED_ROOT_ENTRIES
    if unexpected:
        fail(f"unexpected scratch artifact remains: {sorted(unexpected)[0]}")
    if {path.name for path in PROTECTED.iterdir()} != EXPECTED_PROTECTED_ENTRIES:
        fail("the protected file inventory changed")
    if not RUNTIME.is_dir():
        fail("the genuine expense runtime directory is missing")
    runtime_entries = {path.name for path in RUNTIME.iterdir()}
    if runtime_entries != EXPECTED_RUNTIME_ENTRIES:
        fail("the expense runtime contains missing or unexpected artifacts")


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine expense-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"expense-executable evidence is invalid: {error}")
    if len(entries) < 4:
        fail("expected a help call, two initial checks, and one selective retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    for entry in entries:
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("expense-executable evidence has an invalid signature")
    return entries


def intervals_overlap(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict], results: list[dict]) -> None:
    help_entries = [entry for entry in entries if entry.get("operation") == "help"]
    availability_entries = [
        entry for entry in entries if entry.get("operation") == "availability"
    ]
    if (
        not help_entries
        or len(availability_entries) != 3
        or len(help_entries) + len(availability_entries) != len(entries)
    ):
        fail("a forbidden expense operation was used")

    help_entry = entries[0]
    if help_entry.get("operation") != "help":
        fail("the authoritative help was not used before availability operations")
    for recorded_help in help_entries:
        if (
            recorded_help.get("arguments") != {}
            or recorded_help.get("success") is not True
        ):
            fail("the authoritative top-level help call is invalid")
        for field in ("started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(recorded_help.get(field), int):
                fail(f"help evidence field {field} is missing")
        if recorded_help["started_ns"] > recorded_help["finished_ns"]:
            fail("the help call has an invalid execution interval")

    for entry in availability_entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid", "attempt"):
            if not isinstance(entry.get(field), int):
                fail(f"execution-evidence field {field} is missing")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an availability operation has an invalid execution interval")

    branches = []
    for target in TARGETS:
        branch = [
            entry for entry in availability_entries if entry.get("arguments") == target
        ]
        branches.append(sorted(branch, key=lambda item: item["started_ns"]))
    if len(branches[0]) != 1 or len(branches[1]) != 2:
        fail("the successful branch was repeated or the failed branch was not retried once")

    retained_success = branches[0][0]
    first_failure, retry = branches[1]

    if retained_success.get("success") is not True:
        fail("the independent successful response was not retained")
    if retained_success.get("attempt") != 1:
        fail("the successful branch was checked more than once")
    if retained_success.get("result_digest") != digest(results[0]):
        fail("the retained successful availability evidence is incorrect")

    if first_failure.get("success") is not False:
        fail("the retry branch's first check was not recorded as a failure")
    if first_failure.get("attempt") != 1:
        fail("the failed branch did not begin with attempt one")
    if first_failure.get("error_code") != "expense_registry_busy":
        fail("the failed branch did not report the protected transient class")
    if first_failure.get("retryable") is not True:
        fail("the failed branch was not explicitly marked retryable")

    if retry.get("success") is not True or retry.get("attempt") != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != first_failure.get("arguments"):
        fail("the retry arguments differ from the failed attempt")
    if retry.get("result_digest") != digest(results[1]):
        fail("the retry's successful availability evidence is incorrect")

    if retained_success["pid"] == first_failure["pid"]:
        fail("the initial checks were not separate executable processes")
    if not intervals_overlap(retained_success, first_failure):
        fail("the two initial availability checks were not concurrent")
    if min(retained_success["started_ns"], first_failure["started_ns"]) <= help_entry[
        "finished_ns"
    ]:
        fail("an availability operation began before the help call completed")
    if retry["started_ns"] <= max(
        retained_success["finished_ns"], first_failure["finished_ns"]
    ):
        fail("the retry began before both initial results had returned")


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
    print("PASS: exact bullets and genuine parallel selective-retry trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
