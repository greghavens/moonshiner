#!/usr/bin/env python3
"""Protected verifier for the shipping parallel selective-retry task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".shipping-runtime"
DATABASE = RUNTIME / "shipping.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "shipping-availability.md"
SEED = PROTECTED / "shipping_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "shippingctl"
DOCUMENTATION = ROOT / "SHIPPING.md"
SETUP = PROTECTED / "setup.py"
TARGETS = (
    {
        "name": "Orchid Specimen Crate",
        "location": "Miami Hub",
        "date": "2026-11-26",
    },
    {
        "name": "Archive Film Canister",
        "location": "Boston Hub",
        "date": "2026-11-26",
    },
)
DATA_TABLES = (
    "shipments",
    "availability",
    "transient_failure_policy",
    "saved_preferences",
    "notifications",
    "mutation_log",
)
EXPECTED_CLIENT_SHA256 = "a99f3dc78149c53d6e412864d641796d6cced529dd8ba0db9e4a6c81219beb9c"
EXPECTED_SEED_SHA256 = "cf38dc04f977208a9d157605fcc4cc6ce765cee096c2307243d030e5be107d2f"
EXPECTED_KEY_SHA256 = "9b47ea41b0f4eae88822444b1d0ee099bb85d4f349105d29af07cec7a4f3e08d"
EXPECTED_DOCUMENTATION_SHA256 = "6676bed5e72eeb379e7c163e64cd51c45e5a2a6f93e2fd0e10013d7dfbfcfe8d"
EXPECTED_SETUP_SHA256 = "e85d6259ce6d94967227770c51cdc836fcd739a20aaa72449f87a67bda986dfd"


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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine shipping executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected shipping seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected audit signing key"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "shipping documentation"),
        (SETUP, EXPECTED_SETUP_SHA256, "protected setup"),
    )
    for path, expected, label in protected_files:
        if not path.is_file() or sha256(path) != expected:
            fail(f"{label} changed")
    connection = sqlite3.connect(":memory:")
    connection.executescript(SEED.read_text(encoding="utf-8"))
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return sorted(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))


def expected_results(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    results = []
    for target in TARGETS:
        row = connection.execute(
            "SELECT name, location, availability_date AS date, available, "
            "pickup_slots FROM availability "
            "WHERE name = ? AND location = ? AND availability_date = ?",
            (target["name"], target["location"], target["date"]),
        ).fetchone()
        if row is None:
            fail("protected seed no longer has a requested availability record")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_deliverable(results: list[dict], attempts: list[int]) -> None:
    if not DELIVERABLE.is_file():
        fail("shipping-availability.md is missing")

    lines = [
        "| Shipment | Date | Available | Attempt |",
        "| --- | --- | --- | --- |",
    ]
    for result, attempt in zip(results, attempts, strict=True):
        available = "true" if result["available"] else "false"
        lines.append(
            f"| {result['name']} at {result['location']} | {result['date']} | "
            f"{available} | {attempt} |"
        )
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"shipping-availability.md is not UTF-8: {error}")
    if actual != expected:
        fail("shipping-availability.md is not the exact required two-row table")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine shipping executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only shipping state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine shipping executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"shipping executable evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    for entry in entries:
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("shipping executable evidence has an invalid signature")
    return entries


def intervals_overlap(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict], results: list[dict]) -> list[int]:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a forbidden shipping operation was used")

    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid", "attempt"):
            if type(entry.get(field)) is not int:
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
    if first_success.get("success") is not True or first_success["attempt"] != 1:
        fail("the first requested availability check did not succeed exactly once")
    expected_first = {**results[0], "attempt": first_success["attempt"]}
    if first_success.get("result_digest") != digest(expected_first):
        fail("the retained successful availability evidence is incorrect")

    if first_failure.get("success") is not False:
        fail("the second check's first attempt was not recorded as a failure")
    if first_failure["attempt"] != 1:
        fail("the failed branch did not begin with attempt one")
    if first_failure.get("error_code") != "temporary_unavailable":
        fail("the failed branch did not report a temporary error")
    if first_failure.get("retryable") is not True:
        fail("the failed branch was not explicitly marked retryable")

    if retry.get("success") is not True or retry["attempt"] != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != first_failure.get("arguments"):
        fail("the retry arguments differ from the failed attempt")
    expected_retry = {**results[1], "attempt": retry["attempt"]}
    if retry.get("result_digest") != digest(expected_retry):
        fail("the retry's successful availability evidence is incorrect")

    if first_success["pid"] == first_failure["pid"]:
        fail("the initial checks were not separate executable processes")
    if first_success["parent_pid"] == first_failure["parent_pid"]:
        fail("the initial checks came from one Bash command context")
    if not intervals_overlap(first_success, first_failure):
        fail("the two initial availability checks were not concurrent")
    if retry["started_ns"] <= max(
        first_success["finished_ns"], first_failure["finished_ns"]
    ):
        fail("the retry began before both initial results had returned")
    return [first_success["attempt"], retry["attempt"]]


def main() -> int:
    canonical = canonical_connection()
    try:
        results = expected_results(canonical)
        entries = load_audit()
        attempts = verify_trace(entries, results)
        verify_deliverable(results, attempts)
        verify_read_only(canonical)
    finally:
        canonical.close()
    print("PASS: exact table and genuine parallel selective-retry trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
