#!/usr/bin/env python3
"""Verify genuine concurrent order checks and a selective retry."""

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
RUNTIME = ROOT / ".orders-runtime"
DATABASE = RUNTIME / "orders.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "availability-report.md"
SEED = PROTECTED / "order_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "order-availability"
DOCUMENTATION = ROOT / "ORDERS.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "name": "Lab glassware replenishment",
        "city": "Madison",
        "date": "2026-09-15",
    },
    {
        "name": "Conference lanyard order",
        "city": "Denver",
        "date": "2026-09-15",
    },
)
DATA_TABLES = ("orders", "availability", "reservations")
EXPECTED_CLIENT_SHA256 = "d363b45b79ea0aafb2c2c282a7995a4b666cd09b389918a911857e8659612893"
EXPECTED_SEED_SHA256 = "7165612ab8332b2e1919ccaa13708e14068d155940fcc2c7389fa68f6e801c71"
EXPECTED_KEY_SHA256 = "c2b37e339ad4561e8a84b046bca83beb61a1466a69a9f900f4a13af3516c2cc7"
EXPECTED_DOCUMENTATION_SHA256 = "f7d6aab5fccab5227525ab4ae21420cc010bf1516e9f00d3274c6d9a77fa803b"
EXPECTED_SETUP_SHA256 = "bc3161d64c24003c9e0c3c70a0f503a506a45344e14c65b05e868b0f0d553482"
EXPECTED_GITIGNORE_SHA256 = "3da03a32d20cc2346d1e8793728dae8a7bc87f390df9a261fdfcfe48972f1739"
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".orders-runtime",
    ".protected",
    ".reference_solution.py",
    "ORDERS.md",
    "availability-report.md",
    "order-availability",
}
EXPECTED_PROTECTED_ENTRIES = {
    "audit.key",
    "order_seed.sql",
    "setup.py",
    "verify.py",
}
EXPECTED_RUNTIME_ENTRIES = {
    "attempts.json",
    "attempts.lock",
    "audit.jsonl",
    "initialize.lock",
    "orders.sqlite3",
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
        (CLIENT, EXPECTED_CLIENT_SHA256, "order executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected order seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected signing material"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "order documentation"),
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
        fail(f"cannot load protected order seed: {error}")
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
            "SELECT o.name, o.city, a.availability_date AS date, "
            "a.available FROM availability AS a "
            "JOIN orders AS o ON o.id = a.order_id "
            "WHERE o.name = ? AND o.city = ? AND a.availability_date = ?",
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
        availability = "available" if result["available"] else "unavailable"
        lines.append(f"- {result['name']}: {availability}")
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read availability-report.md as UTF-8: {error}")
    if actual != expected:
        fail("availability-report.md is not the exact grounded availability report")
    if len(actual.splitlines()) != 2 or any(
        not line.startswith("- ") for line in actual.splitlines()
    ):
        fail("availability-report.md is not exactly two Markdown bullets")
    forbidden = (
        "order_registry_busy",
        "availability_not_found",
        "operation_failed",
        "retryable",
        "transient",
        "2026-09-15",
        "Madison",
        "Denver",
        "ord-",
        "Retried",
    )
    if any(value in actual for value in forbidden):
        fail("availability-report.md contains non-availability detail")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine order executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            fail("the order database failed its integrity check")
        if schema(actual) != schema(canonical):
            fail("the order database schema changed")
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only order state changed in table {table}")
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
        fail("the genuine order runtime directory is missing")
    if {path.name for path in RUNTIME.iterdir()} != EXPECTED_RUNTIME_ENTRIES:
        fail("the order runtime has missing or unexpected artifacts")


def load_audit() -> list[dict[str, Any]]:
    if not AUDIT.is_file():
        fail("no signed order-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"order operation evidence is invalid: {error}")
    if len(entries) != 4:
        fail("expected one help invocation, two initial checks, and one selective retry")
    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    verified: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail("an order operation evidence entry is not an object")
        unsigned = dict(entry)
        signature = unsigned.pop("signature", None)
        expected = hmac.new(key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("order operation evidence was not emitted intact")
        verified.append(unsigned)
    return verified


def intervals_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def same_harness_parent(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def direct_tool_call(entry: dict[str, Any]) -> bool:
    return (
        entry.get("process_group_id") == entry.get("pid")
        and entry.get("session_id") == entry.get("pid")
        and entry.get("process_start_ticks") != "unavailable"
    )


def separate_tool_calls(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        direct_tool_call(first)
        and direct_tool_call(second)
        and first.get("process_group_id") != second.get("process_group_id")
    )


def verify_trace(entries: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    help_entries = [entry for entry in entries if entry.get("operation") == "help"]
    availability_entries = [
        entry for entry in entries if entry.get("operation") == "availability"
    ]
    if len(help_entries) != 1 or len(availability_entries) != 3:
        fail("the executable help and availability operations were not used exactly")
    if len(help_entries) + len(availability_entries) != len(entries):
        fail("an operation other than help or availability was used")

    help_entry = help_entries[0]
    if (
        help_entry.get("arguments") not in ({"flag": "-h"}, {"flag": "--help"})
        or help_entry.get("success") is not True
        or not isinstance(help_entry.get("started_ns"), int)
        or not isinstance(help_entry.get("finished_ns"), int)
        or help_entry["started_ns"] >= help_entry["finished_ns"]
        or help_entry["finished_ns"] >= min(
            entry["started_ns"] for entry in availability_entries
        )
    ):
        fail("the executable's built-in help was not completed before order checks")

    entries = availability_entries
    for entry in entries:
        for field in (
            "attempt",
            "started_ns",
            "finished_ns",
            "pid",
            "process_group_id",
            "session_id",
            "parent_pid",
        ):
            if not isinstance(entry.get(field), int) or isinstance(entry.get(field), bool):
                fail(f"signed execution field {field} is missing or invalid")
        for field in ("process_start_ticks", "parent_start_ticks"):
            if not isinstance(entry.get(field), str):
                fail(f"signed execution field {field} is missing or invalid")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an availability operation has an invalid execution interval")

    branches = []
    for target in TARGETS:
        branch = [entry for entry in entries if entry.get("arguments") == target]
        branches.append(sorted(branch, key=lambda value: value["started_ns"]))
    if len(branches[0]) != 1 or len(branches[1]) != 2:
        fail(
            "the successful branch was repeated, the failed branch was not retried "
            "once, or an out-of-scope check was made"
        )

    retained_success = branches[0][0]
    initial_failure, retry = branches[1]
    if retained_success.get("success") is not True or retained_success["attempt"] != 1:
        fail("the retained branch lacks exactly one valid initial success")
    if retained_success.get("result_digest") != digest(results[0]):
        fail("the retained order response is incorrect")

    if initial_failure.get("success") is not False or initial_failure["attempt"] != 1:
        fail("the retry branch does not begin with exactly one failed check")
    if (
        initial_failure.get("error_code") != "order_registry_busy"
        or initial_failure.get("retryable") is not True
        or initial_failure.get("transient") is not True
    ):
        fail("the failed initial branch was not explicitly transient and retryable")

    if retry.get("success") is not True or retry["attempt"] != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != initial_failure.get("arguments"):
        fail("the retry changed an order name, city, or date")
    if retry.get("result_digest") != digest(results[1]):
        fail("the retry's successful order response is incorrect")

    if (
        not intervals_overlap(retained_success, initial_failure)
        or not same_harness_parent(retained_success, initial_failure)
        or not separate_tool_calls(retained_success, initial_failure)
    ):
        fail("the initial checks were not concurrent sibling Pi Bash tool calls")
    if retry["started_ns"] <= max(
        retained_success["finished_ns"], initial_failure["finished_ns"]
    ):
        fail("the retry began before both initial branches returned")
    if not direct_tool_call(retry):
        fail("the selective retry was not one direct Pi Bash tool call")


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
        "verification passed: genuine concurrent checks, retained success, "
        "one selective retry, strict scope, and grounded values verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
