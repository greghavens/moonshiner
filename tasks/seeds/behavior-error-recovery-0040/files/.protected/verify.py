#!/usr/bin/env python3
"""Protected verifier for parallel fleet availability and selective retry."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".fleet-runtime"
DATABASE = RUNTIME / "fleet.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "fleet-availability.md"
SEED = PROTECTED / "fleet_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "fleet-availability"
DOCUMENTATION = ROOT / "FLEET.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "name": "Shuttle 30",
        "depot": "Depot D",
        "date": "2026-11-13",
    },
    {
        "name": "Van 12",
        "depot": "Depot A",
        "date": "2026-11-13",
    },
)
DATA_TABLES = ("vehicles", "availability")
EXPECTED_CLIENT_SHA256 = "53e1973ade4c3dac8f071f8eee4e5fe15dce5b2b738fa0b0d47faca5c8a2f939"
EXPECTED_SEED_SHA256 = "4f3da96107bb7bd277ab82c8e1f0437954a1536e10dd4fba706201d7f5ff7db6"
EXPECTED_KEY_SHA256 = "9dd49571cf1267f87243ccf5b70b81a63f956a8049b8ea511699df1b9277a297"
EXPECTED_DOCUMENTATION_SHA256 = "eaae636ecf7783025f55a3861ad89a5a9db03605df586a9acd96e75e51a54f83"
EXPECTED_SETUP_SHA256 = "28475877543162bdb8602262bca6e298e53eceab14851a9a4d059b9f3cc9e72d"
EXPECTED_GITIGNORE_SHA256 = "c233e0845052e8de4bc622219a7fab764ca68afc3665a83c3360322083927c35"


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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine fleet executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected fleet seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected evidence-signing key"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "fleet documentation"),
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
            "SELECT vehicle_name AS name, depot, availability_date AS date, "
            "available, remaining_capacity FROM availability "
            "WHERE vehicle_name = ? AND depot = ? AND availability_date = ?",
            (target["name"], target["depot"], target["date"]),
        ).fetchone()
        if row is None:
            fail("protected seed no longer has a requested availability row")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_deliverable(results: list[dict]) -> None:
    if not DELIVERABLE.is_file():
        fail("fleet-availability.md is missing")

    lines = []
    for result in results:
        availability = "available" if result["available"] else "unavailable"
        lines.append(f"- {result['name']}: {availability}.")
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"fleet-availability.md is not UTF-8: {error}")
    if actual != expected:
        fail("fleet-availability.md does not contain the exact two bullets")
    if len(actual.splitlines()) != 2 or any(
        not line.startswith("- ") for line in actual.splitlines()
    ):
        fail("the deliverable is not exactly two Markdown bullets")
    forbidden = ("fleet_scheduler_busy", "internal_error", "error_code")
    if any(token in actual for token in forbidden):
        fail("the deliverable exposes an internal error code")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine fleet availability executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only fleet state changed in table {table}")
    finally:
        actual.close()


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine fleet-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"fleet-executable evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one selective retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    for entry in entries:
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("fleet-executable evidence has an invalid signature")
    return entries


def intervals_overlap(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict], results: list[dict]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a forbidden fleet operation was used")

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
    if len(branches[0]) != 2 or len(branches[1]) != 1:
        fail("the successful branch was repeated or the failed branch was not retried once")

    first_failure, retry = branches[0]
    retained_success = branches[1][0]

    if retained_success.get("success") is not True:
        fail("the independent successful response was not retained")
    if retained_success.get("attempt") != 1:
        fail("the successful branch was checked more than once")
    if retained_success.get("result_digest") != digest(results[1]):
        fail("the retained successful availability evidence is incorrect")

    if first_failure.get("success") is not False:
        fail("the retry branch's first check was not recorded as a failure")
    if first_failure.get("attempt") != 1:
        fail("the failed branch did not begin with attempt one")
    if first_failure.get("error_code") != "fleet_scheduler_busy":
        fail("the failed branch did not report the protected transient class")
    if first_failure.get("retryable") is not True:
        fail("the failed branch was not explicitly marked retryable")

    if retry.get("success") is not True or retry.get("attempt") != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != first_failure.get("arguments"):
        fail("the retry arguments differ from the failed attempt")
    if retry.get("result_digest") != digest(results[0]):
        fail("the retry's successful availability evidence is incorrect")

    if retained_success["pid"] == first_failure["pid"]:
        fail("the initial checks were not separate executable processes")
    if not intervals_overlap(retained_success, first_failure):
        fail("the two initial availability checks were not concurrent")
    if retry["started_ns"] <= max(
        retained_success["finished_ns"], first_failure["finished_ns"]
    ):
        fail("the retry began before both initial results had returned")


def verify_workspace_shape() -> None:
    allowed = {
        ".fleet-runtime",
        ".git",
        ".gitignore",
        ".protected",
        ".reference_solution.py",
        ".sandbox-home",
        "FLEET.md",
        "fleet-availability",
        "fleet-availability.md",
    }
    unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    if unexpected:
        fail(f"unexpected sandbox artifacts remain: {', '.join(unexpected)}")


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
    print("PASS: exact bullets and genuine parallel selective-retry trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
