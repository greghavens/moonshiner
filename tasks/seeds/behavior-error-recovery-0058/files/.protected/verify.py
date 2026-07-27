#!/usr/bin/env python3
"""Protected verifier for parallel telecom availability and selective retry."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".telecom-runtime"
DATABASE = RUNTIME / "telecom.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "availability-report.txt"
SEED = PROTECTED / "telecom_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "telecom-availability"
DOCUMENTATION = ROOT / "TELECOM.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "plan": "Fiber plan",
        "segment": "Family",
        "date": "2026-09-13",
    },
    {
        "plan": "Tablet plan",
        "segment": "Studio",
        "date": "2026-09-13",
    },
)
DATA_TABLES = ("plans", "availability")
EXPECTED_CLIENT_SHA256 = "ee0b475b53e1c0fbec27e5bd91d1a443787b3ab965414afaef97246e9369c7a7"
EXPECTED_SEED_SHA256 = "880df953120f6782e4774e84d888adc61a6172d831fdb14a6daa2dc4b420dd2d"
EXPECTED_KEY_SHA256 = "d9a41ab7fccb35602e03cc48a7d42e8d444ecfe04b56ae02b4ef0100e97b95e5"
EXPECTED_DOCUMENTATION_SHA256 = "9a21ef9998b5cb84b95310dfe52a64975e91b07d8ea205e77414b7cb0d1e107e"
EXPECTED_SETUP_SHA256 = "2c71f4a32bef09abb0f079a51ef4dddeffa635156e2775a328cbf8b18348867c"
EXPECTED_GITIGNORE_SHA256 = "9e2498d0620fa6005937a6c17f344a007ed1c1dce003288b48783b34f3c3924c"
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution.py",
    ".telecom-runtime",
    "TELECOM.md",
    "availability-report.txt",
    "telecom-availability",
}
EXPECTED_PROTECTED_ENTRIES = {
    "audit.key",
    "setup.py",
    "telecom_seed.sql",
    "verify.py",
}
EXPECTED_RUNTIME_ENTRIES = {
    "attempts.json",
    "attempts.lock",
    "audit.jsonl",
    "initialize.lock",
    "telecom.sqlite3",
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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine telecom executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected telecom seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected evidence-signing key"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "telecom documentation"),
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
            "SELECT plan, segment, availability_date AS date, available, "
            "daily_capacity FROM availability "
            "WHERE plan = ? AND segment = ? AND availability_date = ?",
            (target["plan"], target["segment"], target["date"]),
        ).fetchone()
        if row is None:
            fail("protected seed no longer has a requested availability row")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def result_sentence(result: dict, retried: bool) -> str:
    availability = "available" if result["available"] else "unavailable"
    retry_marker = " (retried once)" if retried else ""
    return (
        f"{result['plan']} at {result['segment']} is {availability} "
        f"on {result['date']}{retry_marker}."
    )


def verify_deliverable(results: list[dict]) -> None:
    if not DELIVERABLE.is_file():
        fail("availability-report.txt is missing")

    expected = (
        result_sentence(results[0], retried=False)
        + "\n"
        + result_sentence(results[1], retried=True)
        + "\n"
    )
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"availability-report.txt is not UTF-8: {error}")
    if actual != expected:
        fail("availability-report.txt does not contain the exact two sentences")
    if len(actual.splitlines()) != 2:
        fail("the deliverable is not exactly one sentence per check")
    if actual.count("(retried once)") != 1:
        fail("exactly one check must be explicitly marked as retried")
    forbidden = (
        "telecom_calendar_busy",
        "internal_error",
        "error_code",
        "daily_capacity",
    )
    if any(token in actual for token in forbidden):
        fail("the deliverable exposes internal response detail")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine telecom availability executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            fail("the telecom database failed its integrity check")
        if schema(actual) != schema(canonical):
            fail("the read-only telecom database schema changed")
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only telecom state changed in table {table}")
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
        fail("the genuine telecom runtime directory is missing")
    runtime_entries = {path.name for path in RUNTIME.iterdir()}
    if runtime_entries != EXPECTED_RUNTIME_ENTRIES:
        fail("the telecom runtime contains missing or unexpected artifacts")


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine telecom-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"telecom-executable evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one selective retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    for entry in entries:
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("telecom-executable evidence has an invalid signature")
    return entries


def intervals_overlap(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict], results: list[dict]) -> None:
    if any(entry.get("operation") != "check" for entry in entries):
        fail("a forbidden telecom operation was used")

    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid", "attempt"):
            if not isinstance(entry.get(field), int):
                fail(f"execution-evidence field {field} is missing")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an availability operation has an invalid execution interval")

    branches = []
    for target in TARGETS:
        branch = [entry for entry in entries if entry.get("arguments") == target]
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
    if first_failure.get("error_code") != "telecom_calendar_busy":
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
    print("PASS: exact sentences and genuine parallel selective-retry trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
