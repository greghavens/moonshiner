#!/usr/bin/env python3
"""Protected verifier for parallel plan availability and selective retry."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".availability-runtime"
DATABASE = RUNTIME / "availability.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "availability-report.txt"
SEED = PROTECTED / "availability_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "availabilityctl"
DOCUMENTATION = ROOT / "AVAILABILITY.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "plan": "Fiber plan",
        "segment": "Family",
        "date": "2026-09-27",
    },
    {
        "plan": "Tablet plan",
        "segment": "Studio",
        "date": "2026-09-27",
    },
)
DATA_TABLES = ("plans", "availability")
EXPECTED_CLIENT_SHA256 = "ddca9ed56583b229c696ac9fe0efa901a77a7c41b68b8b6544cf0690cc100252"
EXPECTED_SEED_SHA256 = "c9659a048cac5bb76063784aa638a8ae0c66fb4287cff515e90012f10f57e796"
EXPECTED_KEY_SHA256 = "db92db663f8fc35d512b6052bebc8f7dc634fff894d4f937176359f9073c7ef7"
EXPECTED_DOCUMENTATION_SHA256 = "b882e085b3121e4cb3ac14ad683d0d9f7f8bfafde6542ade919134854e601eb3"
EXPECTED_SETUP_SHA256 = "646ccd5836e04c4d8c92590e8452f441d6967f1bf86880562e8de626d1dbaefb"
EXPECTED_GITIGNORE_SHA256 = "03f64c1dbee61a4f5dfbfac104a0007f139397c87595b37c57030892d00df4bc"


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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine availability executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected availability seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected audit signing key"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "availability documentation"),
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
    return [
        tuple(row)
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    ]


def expected_results(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    results = []
    for target in TARGETS:
        row = connection.execute(
            "SELECT plan_name AS plan, segment, availability_date AS date, "
            "available, inventory_remaining FROM availability "
            "WHERE plan_name = ? AND segment = ? AND availability_date = ?",
            (target["plan"], target["segment"], target["date"]),
        ).fetchone()
        if row is None:
            fail("protected seed no longer has a requested availability row")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_deliverable(results: list[dict]) -> None:
    if not DELIVERABLE.is_file():
        fail("availability-report.txt is missing")

    lines = []
    for index, result in enumerate(results):
        availability = "available" if result["available"] else "unavailable"
        retry_clause = "; its check was retried once" if index == 1 else ""
        lines.append(
            f"{result['plan']} / {result['segment']} is {availability} "
            f"on {result['date']}{retry_clause}."
        )
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"availability-report.txt is not UTF-8: {error}")
    if actual != expected:
        fail("availability-report.txt does not contain the exact two sentences")
    if actual.count("retried once") != 1 or "retried once" not in lines[1]:
        fail("only the retried check may be identified as retried once")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine availability executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only availability state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine availability-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"availability-executable evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    for entry in entries:
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("availability-executable evidence has an invalid signature")
    return entries


def intervals_overlap(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict], results: list[dict]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a forbidden availability operation was used")

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
    if first_failure.get("error_code") != "availability_backend_busy":
        fail("the failed branch did not report the required transient error class")
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
    finally:
        canonical.close()
    print("PASS: exact sentences and genuine parallel selective-retry trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
