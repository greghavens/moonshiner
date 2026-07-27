#!/usr/bin/env python3
"""Protected verifier for the purchasing parallel selective-retry task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".purchasing-runtime"
DATABASE = RUNTIME / "purchasing.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "availability-result.txt"
SEED = PROTECTED / "purchasing_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "purchasingctl"
DOCUMENTATION = ROOT / "PURCHASING.md"
SETUP = PROTECTED / "setup.py"
TARGETS = (
    {
        "name": "Braille Labeler Order",
        "location": "Accessibility Office",
        "date": "2026-11-21",
    },
    {
        "name": "Portable Freezer Order",
        "location": "Field Lab",
        "date": "2026-11-21",
    },
)
DATA_TABLES = (
    "requisitions",
    "availability",
    "saved_preferences",
    "notifications",
    "mutation_log",
)
EXPECTED_CLIENT_SHA256 = "375c6dc3ed3f50f324a7787faa7c897292a4e6276307d75cb98354d7a953ac91"
EXPECTED_SEED_SHA256 = "ed5c7fd9ce85bf22789d629e6f5b88ff653f60a255119d5977830163f35bb80c"
EXPECTED_KEY_SHA256 = "feb9756e5207a6541add7b4989954689c406e397554114f3d2680fd4d7fa0ba7"
EXPECTED_DOCUMENTATION_SHA256 = "942d9fdce14d51c466c9dec07c6782c88682f451e6dfaaa0593a89de72e53e61"
EXPECTED_SETUP_SHA256 = "9b7d1823f54da7be83564f4b6f994c0cc42e79da1906034d65705923a5e7048a"


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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine purchasing executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected purchasing seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected audit signing key"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "purchasing documentation"),
        (SETUP, EXPECTED_SETUP_SHA256, "protected setup"),
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
            "SELECT name, location, availability_date AS date, available, "
            "units_available FROM availability "
            "WHERE name = ? AND location = ? AND availability_date = ?",
            (target["name"], target["location"], target["date"]),
        ).fetchone()
        if row is None:
            fail("protected seed no longer has a requested availability record")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_deliverable(results: list[dict]) -> None:
    if not DELIVERABLE.is_file():
        fail("availability-result.txt is missing")

    labels = ["", " (retried once)"]
    lines = []
    for result, label in zip(results, labels, strict=True):
        availability = "available" if result["available"] else "unavailable"
        lines.append(
            f"{result['name']} at {result['location']} on {result['date']}"
            f"{label}: {availability}."
        )
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"availability-result.txt is not UTF-8: {error}")
    if actual != expected:
        fail("availability-result.txt does not have the exact required two lines")
    if len(re.findall(r"\b[\w’-]+\b", actual, flags=re.UNICODE)) > 65:
        fail("availability-result.txt exceeds 65 words")
    if actual.count("retried once") != 1 or "retried once" not in lines[1]:
        fail("only the second result may be identified as retried")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine purchasing executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only purchasing state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine purchasing executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"purchasing executable evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    for entry in entries:
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("purchasing executable evidence has an invalid signature")
    return entries


def intervals_overlap(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict], results: list[dict]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a forbidden purchasing operation was used")

    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid", "attempt"):
            if not isinstance(entry.get(field), int):
                fail(f"operation evidence field {field} is missing")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an availability operation has an invalid execution interval")

    branches = []
    for target in TARGETS:
        branch = [entry for entry in entries if entry.get("arguments") == target]
        branches.append(sorted(branch, key=lambda item: item["started_ns"]))
    if len(branches[0]) != 1 or len(branches[1]) != 2:
        fail("the successful branch was repeated or the failed branch was not retried once")

    first_success = branches[0][0]
    first_failure, retry = branches[1]
    if first_success.get("success") is not True or first_success.get("attempt") != 1:
        fail("the first requested availability check did not succeed exactly once")
    if first_success.get("result_digest") != digest(results[0]):
        fail("the retained successful availability evidence is incorrect")

    if first_failure.get("success") is not False:
        fail("the second check's first attempt was not recorded as a failure")
    if first_failure.get("attempt") != 1:
        fail("the failed branch did not begin with attempt one")
    if first_failure.get("error_code") != "temporary_unavailable":
        fail("the failed branch did not report the required temporary error class")
    if first_failure.get("retryable") is not True:
        fail("the failed branch was not explicitly marked retryable")

    if retry.get("success") is not True or retry.get("attempt") != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != first_failure.get("arguments"):
        fail("the retry arguments differ from the failed attempt")
    if retry.get("result_digest") != digest(results[1]):
        fail("the retry's successful availability evidence is incorrect")

    if first_success["pid"] == first_failure["pid"]:
        fail("the initial checks were not separate executable processes")
    if not intervals_overlap(first_success, first_failure):
        fail("the two initial availability checks were not concurrent")
    if retry["started_ns"] <= max(
        first_success["finished_ns"], first_failure["finished_ns"]
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
    print(
        "PASS: exact result and genuine parallel selective-retry trace verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
