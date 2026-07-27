#!/usr/bin/env python3
"""Protected verifier for the planning parallel selective-retry task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".planning-runtime"
DATABASE = RUNTIME / "planning.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "planning-availability.txt"
SEED = PROTECTED / "planning_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "planningctl"
DOCUMENTATION = ROOT / "PLANNING.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "name": "Emergency Signage Translation",
        "program": "Safety Program",
        "date": "2026-12-02",
    },
    {
        "name": "Oral History Release Audit",
        "program": "Archive Program",
        "date": "2026-12-02",
    },
)
DATA_TABLES = (
    "planning_items",
    "availability",
    "saved_preferences",
    "notifications",
    "mutation_log",
)
EXPECTED_CLIENT_SHA256 = "be1c53fb31d3a3453cbe0e8eb279b6ae4038e7ff6f5457751be5947991360c9c"
EXPECTED_SEED_SHA256 = "8261aeb77d359284f7ae1d1990d9a48851f89c49871aa93a1fe5a8f4e138d9e4"
EXPECTED_KEY_SHA256 = "c90be065244d09acb80262ac47895f2e6c1bebc24363c2847b4c6ee489287b31"
EXPECTED_DOCUMENTATION_SHA256 = "e8a9d4117ef1e63b5ae9e221d3c59844f101e033838741495671d67265484213"
EXPECTED_SETUP_SHA256 = "0421984727dc6bcadeb91491c568bee0dba1058524cf91e2e9453324779769d4"
EXPECTED_GITIGNORE_SHA256 = "1dbe61b0f9df65eee105cc2a0afae183914c1cf75ff3a3f2e7edeb1dffe540d9"


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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine planning executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected planning seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected audit signing key"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "planning documentation"),
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
            "SELECT task_name AS name, program, availability_date AS date, "
            "available, reviewer_slots FROM availability "
            "WHERE task_name = ? AND program = ? AND availability_date = ?",
            (target["name"], target["program"], target["date"]),
        ).fetchone()
        if row is None:
            fail("protected seed no longer has a requested availability row")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_deliverable(results: list[dict]) -> None:
    if not DELIVERABLE.is_file():
        fail("planning-availability.txt is missing")

    lines = []
    for index, result in enumerate(results):
        availability = "available" if result["available"] else "unavailable"
        retry_clause = "; its check was retried once" if index == 1 else ""
        lines.append(
            f"{result['name']} at {result['program']} on {result['date']}: "
            f"{availability}{retry_clause}."
        )
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"planning-availability.txt is not UTF-8: {error}")
    if actual != expected:
        fail("planning-availability.txt does not contain the exact two sentences")
    if actual.count("retried once") != 1 or "retried once" not in lines[1]:
        fail("only the second task may be identified as retried once")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine planning executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only planning state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine planning executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"planning executable evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    for entry in entries:
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("planning executable evidence has an invalid signature")
    return entries


def intervals_overlap(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict], results: list[dict]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a forbidden planning operation was used")

    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid", "attempt"):
            if not isinstance(entry.get(field), int):
                fail(f"operation-evidence field {field} is missing")
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
    if first_success["parent_pid"] == first_failure["parent_pid"]:
        fail("the initial checks came from one Bash command context")
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
    print("PASS: exact result and genuine parallel selective-retry trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
