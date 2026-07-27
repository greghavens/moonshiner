#!/usr/bin/env python3
"""Verify genuine parallel subscription checks and a selective retry."""

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
RUNTIME = ROOT / ".subscriptions-runtime"
DATABASE = RUNTIME / "subscriptions.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
ATTEMPTS = RUNTIME / "attempts.json"
DELIVERABLE = ROOT / "availability-report.md"
SEED = PROTECTED / "subscriptions_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "subscriptionctl"
DOCUMENTATION = ROOT / "SUBSCRIPTIONS.md"
SETUP = PROTECTED / "reference_setup.py"
GITIGNORE = ROOT / ".gitignore"
REFERENCE_DRIVER = ROOT / ".reference_solution.py"
TARGETS = (
    {
        "plan": "Fiber plan 118",
        "group": "Family group",
        "date": "2026-09-19",
    },
    {
        "plan": "Tablet plan 118",
        "group": "Studio",
        "date": "2026-09-19",
    },
)
DATA_TABLES = ("availability", "subscription_preferences", "subscriptions")
EXPECTED_CLIENT_SHA256 = "130abf673da6a04eb2e75a82ea3cdbe4176f1a3221a7472d7de8cf70ccfe1329"
EXPECTED_SEED_SHA256 = "1a7cf1bc4235879db1ea9c530497d0448e04f36bd4544ff1b0373d90449a882c"
EXPECTED_KEY_SHA256 = "b16102f29748e2f791eed0333ab56d4449ee923ec0f2a02d1a64c5391b1dceef"
EXPECTED_DOCUMENTATION_SHA256 = "acef9b8981ae2e9278a8f93219cca4023d2c9dab56ee64f6a1ac62c163ed86c3"
EXPECTED_SETUP_SHA256 = "277fc1c2043907dc826c95a102382b6ffcbc101fc03d09b35e3f3d61019a1209"
EXPECTED_GITIGNORE_SHA256 = "22bf1a00ee7ac02b2ed30767f794590e813915de8d9efafec4fb157a935da3eb"
EXPECTED_REFERENCE_DRIVER_SHA256 = (
    "16b1846b0f93e8263d9b5c157fd4b37dcfbef67d99eafb274f4d6739195d9dd7"
)
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution.py",
    ".subscriptions-runtime",
    "SUBSCRIPTIONS.md",
    "availability-report.md",
    "subscriptionctl",
}
EXPECTED_PROTECTED_ENTRIES = {
    "audit.key",
    "reference_setup.py",
    "subscriptions_seed.sql",
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
        (CLIENT, EXPECTED_CLIENT_SHA256, "subscription executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected subscription seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected signing material"),
        (
            DOCUMENTATION,
            EXPECTED_DOCUMENTATION_SHA256,
            "subscription documentation",
        ),
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
            "SELECT s.plan, s.group_name AS \"group\", "
            "a.availability_date AS date, a.available "
            "FROM availability AS a "
            "JOIN subscriptions AS s ON s.id = a.subscription_id "
            "WHERE s.plan = ? AND s.group_name = ? "
            "AND a.availability_date = ?",
            (target["plan"], target["group"], target["date"]),
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
        lines.append(f"- {result['plan']}: {availability}")
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
        "subscription_registry_busy",
        "availability_not_found",
        "operation_failed",
        "retryable",
        "transient",
        "2026-09-19",
        "Family group",
        "Studio",
        "sub-",
        "retry",
    )
    if any(value in actual for value in forbidden):
        fail("availability-report.md contains unrequested or ungrounded detail")


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
    if REFERENCE_DRIVER.name in root_entries and (
        REFERENCE_DRIVER.is_symlink()
        or not REFERENCE_DRIVER.is_file()
        or sha256(REFERENCE_DRIVER) != EXPECTED_REFERENCE_DRIVER_SHA256
    ):
        fail("unexpected scratch artifact remains: .reference_solution.py")
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
    if len(entries) != 3:
        fail("expected exactly two initial checks and one selective retry")
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


def verify_attempt_state() -> None:
    try:
        attempts = json.loads(ATTEMPTS.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"subscription attempt state is invalid: {error}")
    expected = {
        json.dumps(target, ensure_ascii=False, sort_keys=True): count
        for target, count in zip(TARGETS, (1, 2), strict=True)
    }
    if attempts != expected:
        fail("subscription attempt state does not prove one selective retry")


def intervals_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("an operation other than availability was used")
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
        fail(
            "the successful branch was repeated, the failed branch was not retried "
            "once, or an out-of-scope check was made"
        )

    retained_success = branches[0][0]
    initial_failure, retry = branches[1]
    if retained_success.get("success") is not True or retained_success["attempt"] != 1:
        fail("the retained branch lacks exactly one valid initial success")
    if retained_success.get("result_digest") != digest(results[0]):
        fail("the retained subscription response is incorrect")

    if initial_failure.get("success") is not False or initial_failure["attempt"] != 1:
        fail("the retry branch does not begin with exactly one failed check")
    if (
        initial_failure.get("error_code") != "subscription_registry_busy"
        or initial_failure.get("retryable") is not True
        or initial_failure.get("transient") is not True
    ):
        fail("the failed initial branch was not explicitly transient and retryable")

    if retry.get("success") is not True or retry["attempt"] != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != initial_failure.get("arguments"):
        fail("the retry changed a plan, group, or date")
    if retry.get("result_digest") != digest(results[1]):
        fail("the retry's successful subscription response is incorrect")

    if not intervals_overlap(retained_success, initial_failure):
        fail("the two independent initial checks were not issued in parallel")
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
        entries = load_audit()
        verify_attempt_state()
        verify_trace(entries, results)
        verify_layout()
    finally:
        canonical.close()
    print(
        "verification passed: genuine parallel subscription checks, retained "
        "success, one selective retry, read-only state, and grounded values verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
