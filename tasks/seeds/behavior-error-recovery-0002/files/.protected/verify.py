#!/usr/bin/env python3
"""Protected deterministic verifier for the travel retry behavior task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".travel-runtime"
DATABASE = RUNTIME / "travel.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "availability-result.md"
SEED = PROTECTED / "travel_seed.sql"
KEY = PROTECTED / "receipt.key"
CLIENT = ROOT / "travelctl"
TARGETS = (
    {
        "name": "Reykjavík Accessibility Workshop Trip",
        "location": "Reykjavík",
        "date": "2026-11-12",
    },
    {
        "name": "Marrakesh Archive Consultation Trip",
        "location": "Marrakesh",
        "date": "2026-11-12",
    },
)
DATA_TABLES = (
    "trips",
    "availability",
    "saved_preferences",
    "notifications",
    "mutation_log",
)
EXPECTED_CLIENT_SHA256 = "6d718812620be32a8adccf88fabf9ea3e8eaf61d9179ccfa551246cb3d61a3bc"
EXPECTED_SEED_SHA256 = "22009fd18d26f37a4d7a45166b19aa81c7568fa75e1a75b32df7279b72bfc7f5"
EXPECTED_KEY_SHA256 = "8e610b56df0341cb65633c82c59deabb04e241e35f0656fc39cb9221e567bbaf"


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
    if sha256(CLIENT) != EXPECTED_CLIENT_SHA256:
        fail("genuine travel executable changed")
    if sha256(SEED) != EXPECTED_SEED_SHA256:
        fail("protected travel seed changed")
    if sha256(KEY) != EXPECTED_KEY_SHA256:
        fail("protected audit signing key changed")
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
            "SELECT name, location, travel_date AS date, available, "
            "remaining_capacity FROM availability "
            "WHERE name = ? AND location = ? AND travel_date = ?",
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
        fail("availability-result.md is missing")

    labels = ["", " (retried once)"]
    lines = []
    for result, label in zip(results, labels, strict=True):
        availability = "available" if result["available"] else "unavailable"
        lines.append(
            f"- {result['name']} at {result['location']} on {result['date']}"
            f"{label}: {availability}."
        )
    expected = "\n".join(lines) + "\n"
    if DELIVERABLE.read_text(encoding="utf-8") != expected:
        fail("availability-result.md does not have the exact required two bullets")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine travel executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only travel state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine travel executable audit was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"travel executable audit is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    for entry in entries:
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail("travel executable audit signature is invalid")
    return entries


def intervals_overlap(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict], results: list[dict]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a forbidden travel operation was used")

    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid", "attempt"):
            if not isinstance(entry.get(field), int):
                fail(f"audit process/timing field {field} is missing")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an audited travel operation has an invalid interval")

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
        fail("the failed branch's first attempt was not recorded as a failure")
    if first_failure.get("attempt") != 1:
        fail("the failed branch did not begin with attempt one")
    if first_failure.get("error_code") != "temporary_unavailable":
        fail("the failed branch did not report the expected temporary error class")
    if first_failure.get("retryable") is not True:
        fail("the failed branch was not explicitly marked retryable")

    if retry.get("success") is not True or retry.get("attempt") != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != first_failure.get("arguments"):
        fail("the retry arguments differ from the failed attempt")
    if retry.get("result_digest") != digest(results[1]):
        fail("the retry's successful availability evidence is incorrect")

    if not intervals_overlap(first_success, first_failure):
        fail("the two initial availability checks were not concurrent")
    if first_success["parent_pid"] != first_failure["parent_pid"]:
        fail("the initial checks did not originate in one Bash action")
    if first_success["pid"] == first_failure["pid"]:
        fail("the initial checks were not separate executable processes")
    if retry["started_ns"] <= max(
        first_success["finished_ns"], first_failure["finished_ns"]
    ):
        fail("the retry began before both initial results had returned")
    if retry["parent_pid"] == first_success["parent_pid"]:
        fail("the retry was not issued in a later Bash action")


def main() -> int:
    canonical = canonical_connection()
    try:
        results = expected_results(canonical)
        verify_deliverable(results)
        verify_read_only(canonical)
        verify_trace(load_audit(), results)
    finally:
        canonical.close()
    print("PASS: exact two-bullet result and genuine parallel selective-retry trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
