#!/usr/bin/env python3
"""Protected verifier for the education parallel selective-retry task."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".education-runtime"
DATABASE = RUNTIME / "education.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "education-availability.txt"
SEED = PROTECTED / "education_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "educationctl"
DOCUMENTATION = ROOT / "EDUCATION.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "name": "Microeconomics",
        "location": "Downtown",
        "date": "2026-11-25",
    },
    {
        "name": "Modern History",
        "location": "North Campus",
        "date": "2026-11-25",
    },
)
DATA_TABLES = (
    "courses",
    "availability",
    "saved_preferences",
    "notifications",
    "mutation_log",
)
EXPECTED_CLIENT_SHA256 = "8c1c81a2b12127e6e9bd1a71ec5011e77d49a4fb09f5303a580ca3c9bbab362f"
EXPECTED_SEED_SHA256 = "f758648dac30532740b2c0896f87c32f25a85641c92e533bdf89211527f1f768"
EXPECTED_KEY_SHA256 = "37f49f839199ceb80801866c51a3605c979d9819b1fa9960d56b5d057c61d938"
EXPECTED_DOCUMENTATION_SHA256 = "f9473d3d69747a592b5740d3fc2b09b49c230723270015b7ca419c2a7b50dcb0"
EXPECTED_SETUP_SHA256 = "e99d940587f3eccfa688f9025d5f046e0f7d213ec048a7b05e3716a751cf7a0a"
EXPECTED_GITIGNORE_SHA256 = "91d0c5f11bcf4ec675846a33608aa250c5569ab83aea33593dfa982d8e5c514e"


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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine education executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected education seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected audit signing key"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "education documentation"),
        (SETUP, EXPECTED_SETUP_SHA256, "protected setup"),
        (GITIGNORE, EXPECTED_GITIGNORE_SHA256, "sandbox ignore rules"),
    )
    for path, expected, label in protected_files:
        if not path.is_file() or sha256(path) != expected:
            fail(f"{label} changed")
    if not os.access(CLIENT, os.X_OK):
        fail("genuine education executable is not executable")
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
            "SELECT course_name AS name, location, availability_date AS date, "
            "available, seats_remaining FROM availability "
            "WHERE course_name = ? AND location = ? AND availability_date = ?",
            (target["name"], target["location"], target["date"]),
        ).fetchone()
        if row is None:
            fail("protected seed no longer has a requested availability row")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_deliverable(results: list[dict]) -> None:
    if not DELIVERABLE.is_file():
        fail("education-availability.txt is missing")

    lines = []
    for index, result in enumerate(results):
        availability = "available" if result["available"] else "unavailable"
        retry_clause = "; its check was retried once" if index == 0 else ""
        lines.append(
            f"{result['name']} at {result['location']} on {result['date']}: "
            f"{availability}{retry_clause}."
        )
    if lines != sorted(lines):
        fail("protected target order is not alphabetical by course name")
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"education-availability.txt is not UTF-8: {error}")
    if actual != expected:
        fail("education-availability.txt does not contain the exact two sentences")
    if actual.count("retried once") != 1 or "retried once" not in lines[0]:
        fail("only the retried course may be identified as retried once")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine education executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only education state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine education executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"education executable evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    for entry in entries:
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("education executable evidence has an invalid signature")
    return entries


def intervals_overlap(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict], results: list[dict]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a forbidden education operation was used")

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
    if len(branches[0]) != 2 or len(branches[1]) != 1:
        fail("the successful branch was repeated or the failed branch was not retried once")

    first_failure, retry = branches[0]
    retained_success = branches[1][0]

    if first_failure.get("success") is not False:
        fail("the retry branch's first check was not recorded as a failure")
    if first_failure.get("attempt") != 1:
        fail("the failed branch did not begin with attempt one")
    if first_failure.get("error_code") != "temporary_unavailable":
        fail("the failed branch did not report the required temporary error class")
    if first_failure.get("retryable") is not True:
        fail("the failed branch was not explicitly marked retryable")

    if retained_success.get("success") is not True:
        fail("the independent successful response was not retained")
    if retained_success.get("attempt") != 1:
        fail("the successful branch was checked more than once")
    if retained_success.get("result_digest") != digest(results[1]):
        fail("the retained successful availability evidence is incorrect")

    if retry.get("success") is not True or retry.get("attempt") != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != first_failure.get("arguments"):
        fail("the retry arguments differ from the failed attempt")
    if retry.get("result_digest") != digest(results[0]):
        fail("the retry's successful availability evidence is incorrect")

    if first_failure["pid"] == retained_success["pid"]:
        fail("the initial checks were not separate executable processes")
    if not intervals_overlap(first_failure, retained_success):
        fail("the two initial availability checks were not concurrent")
    if retry["started_ns"] <= max(
        first_failure["finished_ns"], retained_success["finished_ns"]
    ):
        fail("the retry began before both initial results had returned")


def verify_workspace_shape() -> None:
    allowed = {
        ".education-runtime",
        ".git",
        ".gitignore",
        ".protected",
        ".reference_solution.py",
        "EDUCATION.md",
        "education-availability.txt",
        "educationctl",
    }
    extras = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    if extras:
        fail("unexpected scratch artifact remains: " + ", ".join(extras))


def main() -> int:
    canonical = canonical_connection()
    try:
        results = expected_results(canonical)
        verify_deliverable(results)
        verify_read_only(canonical)
        verify_trace(load_audit(), results)
        verify_workspace_shape()
    finally:
        canonical.close()
    print("PASS: exact result and genuine parallel selective-retry trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

