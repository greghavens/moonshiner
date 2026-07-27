#!/usr/bin/env python3
"""Protected verification for paired checks and selective error recovery."""

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
RUNTIME = ROOT / ".travel-runtime"
DATABASE = RUNTIME / "travel.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "availability-report.md"
SEED = PROTECTED / "travel_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "trip-availability"
DOCUMENTATION = ROOT / "TRAVEL.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "name": "Kyoto archives visit",
        "city": "Kyoto",
        "date": "2026-09-24",
    },
    {
        "name": "Osaka supplier tour",
        "city": "Osaka",
        "date": "2026-09-24",
    },
)
DATA_TABLES = ("trips", "availability", "notifications")
EXPECTED_CLIENT_SHA256 = "900c44334316d0e5cc24757eb1544b597b527e57253587464dd9629d66a55584"
EXPECTED_SEED_SHA256 = "183a2d6099eb9990437a67837a9053d83c9436f1b02c0ba0b201f99739b99ee7"
EXPECTED_KEY_SHA256 = "36ac196c5df93ce76f9f94650f08110fdfebaf4f6e5c13f2852c2eb2fc574111"
EXPECTED_DOCUMENTATION_SHA256 = "4ce571ace824ba38a101434642014c96ed595e85f8291f9ed2809adaf13fcfae"
EXPECTED_SETUP_SHA256 = "ffa715edf72e4f5ae8e64b4884bc35c021a11894b1bd9ac421307c2314ad98cb"
EXPECTED_GITIGNORE_SHA256 = "c5367715b81d700f12268ace06aaece9e2548209ae247d92951b924a6c6266da"
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution.py",
    ".travel-runtime",
    "TRAVEL.md",
    "availability-report.md",
    "trip-availability",
}
EXPECTED_PROTECTED_ENTRIES = {
    "audit.key",
    "setup.py",
    "travel_seed.sql",
    "verify.py",
}
EXPECTED_RUNTIME_ENTRIES = {
    "attempts.json",
    "attempts.lock",
    "audit.jsonl",
    "initialize.lock",
    "travel.sqlite3",
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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine travel executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected travel seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected signing material"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "travel documentation"),
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
        fail(f"cannot load protected travel seed: {error}")
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
            "SELECT trip_name AS name, city, availability_date AS date, "
            "available FROM availability WHERE trip_name = ? AND city = ? "
            "AND availability_date = ?",
            (target["name"], target["city"], target["date"]),
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
            f"- {result['name']} in {result['city']}: "
            f"{availability_text} on {result['date']}."
        )
    lines.append(f"- Retried branch: {results[0]['name']} in {results[0]['city']}.")
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read availability-report.md as UTF-8: {error}")
    if actual != expected:
        fail("availability-report.md is not the exact grounded recovery report")
    if len(actual.splitlines()) != 3 or any(
        not line.startswith("- ") for line in actual.splitlines()
    ):
        fail("availability-report.md is not exactly three Markdown bullets")
    forbidden = (
        "travel_service_busy",
        "operation_failed",
        "retryable",
        "transient",
        "tra-",
        "draft",
        "review",
        "Tokyo",
    )
    if any(value in actual for value in forbidden):
        fail("availability-report.md contains raw errors or out-of-scope records")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine travel executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            fail("the travel database failed its integrity check")
        if schema(actual) != schema(canonical):
            fail("the travel database schema changed")
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only travel state changed in table {table}")
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
        fail("the genuine travel runtime directory is missing")
    if {path.name for path in RUNTIME.iterdir()} != EXPECTED_RUNTIME_ENTRIES:
        fail("the travel runtime has missing or unexpected artifacts")


def load_audit() -> list[dict[str, Any]]:
    if not AUDIT.is_file():
        fail("no signed travel-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"travel operation evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one selective retry")
    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    verified: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail("a travel operation evidence entry is not an object")
        unsigned = dict(entry)
        signature = unsigned.pop("signature", None)
        expected = hmac.new(key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("travel operation evidence was not emitted intact")
        verified.append(unsigned)
    return verified


def intervals_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a get, create, cancel, update, notify, or other operation was used")
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
    if len(branches[0]) != 2 or len(branches[1]) != 1:
        fail(
            "the successful branch was repeated, an extra record was checked, "
            "or the failed branch was not retried once"
        )

    initial_failure, retry = branches[0]
    retained_success = branches[1][0]
    if retained_success.get("success") is not True or retained_success["attempt"] != 1:
        fail("the retained branch lacks one valid initial success")
    if retained_success.get("result_digest") != digest(results[1]):
        fail("the retained successful availability response is incorrect")

    if initial_failure.get("success") is not False or initial_failure["attempt"] != 1:
        fail("the retry branch does not begin with exactly one failed check")
    if (
        initial_failure.get("error_code") != "travel_service_busy"
        or initial_failure.get("retryable") is not True
        or initial_failure.get("transient") is not True
    ):
        fail("the failed initial branch was not explicitly transient and retryable")

    if retry.get("success") is not True or retry["attempt"] != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != initial_failure.get("arguments"):
        fail("the retry changed a trip name, city, or date")
    if retry.get("result_digest") != digest(results[0]):
        fail("the retry's successful availability response is incorrect")

    if not intervals_overlap(initial_failure, retained_success):
        fail("the two independent initial checks were not issued together")
    if retry["started_ns"] <= max(
        initial_failure["finished_ns"], retained_success["finished_ns"]
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
        "verification passed: genuine paired checks, retained success, "
        "one selective retry, strict scope, and grounded report verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
