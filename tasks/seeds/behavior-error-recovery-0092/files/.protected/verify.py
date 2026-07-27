#!/usr/bin/env python3
"""Verify parallel course checks, selective recovery, and exact reporting."""

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
RUNTIME = ROOT / ".course-runtime"
DATABASE = RUNTIME / "courses.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
ATTEMPTS = RUNTIME / "attempts.json"
DELIVERABLE = ROOT / "course-availability.md"
SEED = PROTECTED / "course_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "coursectl"
DOCUMENTATION = ROOT / "COURSES.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
REFERENCE_DRIVER = ROOT / ".reference_solution.py"
TARGETS = (
    {
        "course": "Wetland field methods",
        "campus": "River Campus",
        "date": "2026-09-21",
    },
    {
        "course": "Introductory American Sign Language",
        "campus": "Central Campus",
        "date": "2026-09-21",
    },
)
DATA_TABLES = ("campuses", "courses", "availability")
EXPECTED_CLIENT_SHA256 = "7f1de7ec2dd3214d18ff3baabbeb335008877416091ac202a0453eb5f585d544"
EXPECTED_SEED_SHA256 = "126c574de2c82a7639635248baf6cc477cd4b26d77aa0c3c50baec504bfd6ebb"
EXPECTED_KEY_SHA256 = "ed9da761c5e83d09b4cd8129c5330e3fc561254e42d7e49c2e3b03b9bee3732c"
EXPECTED_DOCUMENTATION_SHA256 = "b8ff35c1f86192b300d2585ddcd72bce50cc9828d8d1e0fac94dbdea4ac85957"
EXPECTED_SETUP_SHA256 = "cb411cc398e529746522b6d9d36538b6404ffc7fc353f3ff17b054b7adaeca8f"
EXPECTED_GITIGNORE_SHA256 = "e9a46ac52551e1535bbb268f45ef566f6d3686620525dba56462b168a82b0ae2"
EXPECTED_REFERENCE_DRIVER_SHA256 = "70223af8fba4f838cce713c13db4d80e0e5bf1b0d512663bbadf6936f07e44c8"
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".course-runtime",
    ".protected",
    "COURSES.md",
    "course-availability.md",
    "coursectl",
}
EXPECTED_PROTECTED_ENTRIES = {
    "audit.key",
    "course_seed.sql",
    "setup.py",
    "verify.py",
}
EXPECTED_RUNTIME_ENTRIES = {
    "attempts.json",
    "attempts.lock",
    "audit.jsonl",
    "courses.sqlite3",
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
        (CLIENT, EXPECTED_CLIENT_SHA256, "course executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected course seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected signing material"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "course documentation"),
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
        fail(f"cannot load protected course seed: {error}")
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
            "SELECT c.title AS course, p.name AS campus, "
            "a.availability_date AS date, a.available "
            "FROM availability AS a "
            "JOIN courses AS c ON c.id = a.course_id "
            "JOIN campuses AS p ON p.id = c.campus_id "
            "WHERE c.title = ? AND p.name = ? AND a.availability_date = ?",
            (target["course"], target["campus"], target["date"]),
        ).fetchone()
        if row is None:
            fail("the protected seed lacks a requested availability record")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine course executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            fail("the course database failed its integrity check")
        if schema(actual) != schema(canonical):
            fail("the course database schema changed")
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"course state changed in table {table}")
    finally:
        actual.close()


def verify_layout() -> None:
    allowed_root_entries = set(ALLOWED_ROOT_ENTRIES)
    if REFERENCE_DRIVER.exists():
        if (
            not REFERENCE_DRIVER.is_file()
            or sha256(REFERENCE_DRIVER) != EXPECTED_REFERENCE_DRIVER_SHA256
        ):
            fail("unexpected scratch artifact remains: .reference_solution.py")
        allowed_root_entries.add(REFERENCE_DRIVER.name)
    root_entries = {path.name for path in ROOT.iterdir()}
    unexpected = sorted(root_entries - allowed_root_entries)
    if unexpected:
        fail(f"unexpected scratch artifact remains: {unexpected[0]}")
    if {path.name for path in PROTECTED.iterdir()} != EXPECTED_PROTECTED_ENTRIES:
        fail("the protected file inventory changed")
    if not RUNTIME.is_dir():
        fail("the genuine course runtime directory is missing")
    if {path.name for path in RUNTIME.iterdir()} != EXPECTED_RUNTIME_ENTRIES:
        fail("the course runtime has missing or unexpected artifacts")


def load_audit() -> list[dict[str, Any]]:
    if not AUDIT.is_file():
        fail("no signed course-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"course operation evidence is invalid: {error}")
    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    verified: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail("a course operation evidence entry is not an object")
        unsigned = dict(entry)
        signature = unsigned.pop("signature", None)
        expected = hmac.new(key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("course operation evidence was not emitted intact")
        verified.append(unsigned)
    return verified


def verify_help(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    help_entries = [entry for entry in entries if entry.get("operation") == "help"]
    availability_entries = [
        entry for entry in entries if entry.get("operation") == "availability"
    ]
    if len(availability_entries) != 3:
        fail("expected exactly two initial checks and one selective retry")
    if len(help_entries) < 1:
        fail("the executable's built-in help was not run")
    if len(help_entries) + len(availability_entries) != len(entries):
        fail("an unsupported course executable operation was used")
    first_availability_start = min(
        entry.get("started_ns", -1) for entry in availability_entries
    )
    for entry in help_entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int) or isinstance(
                entry.get(field), bool
            ):
                fail(f"signed help execution field {field} is missing or invalid")
        if (
            entry.get("arguments") != {}
            or entry.get("success") is not True
            or entry.get("result_digest") != digest({"interface": "coursectl --help"})
            or entry["started_ns"] >= entry["finished_ns"]
        ):
            fail("the executable help evidence is invalid")
    if not any(
        entry["finished_ns"] <= first_availability_start for entry in help_entries
    ):
        fail("the executable's built-in help was not run before course checks")
    return availability_entries


def verify_attempt_state() -> None:
    try:
        actual = json.loads(ATTEMPTS.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"course attempt state is invalid: {error}")
    expected = {}
    for target, attempt in zip(TARGETS, (1, 2)):
        key = json.dumps(
            {"operation": "availability", **target},
            ensure_ascii=False,
            sort_keys=True,
        )
        expected[key] = attempt
    if actual != expected:
        fail("course attempt state does not match the exact recovery workflow")


def intervals_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict[str, Any]], results: list[dict[str, Any]]) -> str:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a non-availability course operation was used")
    for entry in entries:
        for field in ("attempt", "started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int) or isinstance(entry.get(field), bool):
                fail(f"signed execution field {field} is missing or invalid")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an availability operation has an invalid execution interval")

    branches: list[list[dict[str, Any]]] = []
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
        fail("the retained course response is incorrect")

    if initial_failure.get("success") is not False or initial_failure["attempt"] != 1:
        fail("the retry branch does not begin with exactly one failed check")
    if (
        initial_failure.get("error_code") != "registry_temporarily_busy"
        or initial_failure.get("retryable") is not True
        or initial_failure.get("transient") is not True
    ):
        fail("the failed initial branch was not explicitly transient and retryable")

    if retry.get("success") is not True or retry["attempt"] != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != initial_failure.get("arguments"):
        fail("the retry changed a course title, campus, or date")
    if retry.get("result_digest") != digest(results[1]):
        fail("the retry's successful course response is incorrect")

    if not intervals_overlap(retained_success, initial_failure):
        fail("the two independent initial checks were not issued together")
    if retry["started_ns"] <= max(
        retained_success["finished_ns"], initial_failure["finished_ns"]
    ):
        fail("the retry began before both initial branches returned")
    return str(initial_failure["arguments"]["course"])


def verify_deliverable(results: list[dict[str, Any]], retried_course: str) -> None:
    if not DELIVERABLE.is_file():
        fail("course-availability.md is missing")
    lines = []
    for result in results:
        availability_value = "available" if result["available"] else "unavailable"
        lines.append(
            f"- {result['course']} | {result['campus']} | "
            f"{result['date']} | {availability_value}"
        )
    lines.append(f"- Retried branch: {retried_course}")
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read course-availability.md as UTF-8: {error}")
    if actual != expected:
        fail("course-availability.md is not the exact grounded report")
    if len(actual.splitlines()) != 3 or any(
        not line.startswith("- ") for line in actual.splitlines()
    ):
        fail("course-availability.md is not exactly three Markdown bullets")
    forbidden = (
        "registry_temporarily_busy",
        "availability_not_found",
        "operation_failed",
        "retryable",
        "transient",
        "error",
    )
    if any(value in actual for value in forbidden):
        fail("course-availability.md contains raw operation detail")


def main() -> int:
    canonical = canonical_connection()
    try:
        results = expected_results(canonical)
        verify_read_only(canonical)
        entries = load_audit()
        retried_course = verify_trace(verify_help(entries), results)
        verify_attempt_state()
        verify_deliverable(results, retried_course)
        verify_layout()
    finally:
        canonical.close()
    print(
        "verification passed: genuine concurrent checks, retained success, "
        "one selective retry, exact scope, and grounded three-bullet report verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
