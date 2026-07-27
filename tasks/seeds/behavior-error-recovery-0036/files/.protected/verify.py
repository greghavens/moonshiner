#!/usr/bin/env python3
"""Protected verifier for parallel claims availability and selective retry."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".claims-runtime"
DATABASE = RUNTIME / "claims.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
ATTEMPTS = RUNTIME / "attempts.json"
DELIVERABLE = ROOT / "claim-availability.md"
SEED = PROTECTED / "claims_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "claims-availability"
DOCUMENTATION = ROOT / "CLAIMS.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "item": "Theft claim",
        "office": "West Office",
        "date": "2026-11-27",
    },
    {
        "item": "Windshield claim",
        "office": "North Office",
        "date": "2026-11-27",
    },
)
DATA_TABLES = ("claim_items", "availability")
EXPECTED_HASHES = {
    "claims-availability": "27e764e6063a7a9e32959fac903b2087caeb8e63e447d8b2dc0f361b948008e8",
    ".protected/claims_seed.sql": "e4e886464d89365a7a9f243bef18db9fc77887cddfbb33d4ab994f3b8bb854af",
    ".protected/audit.key": "739d89e327e898a421bebd0a872aac23d4855cda238563898ae66882a0289383",
    "CLAIMS.md": "896fba7864871c7c6f9b28e11169f7ab6b775d4007b1dbf8db94c6ca355e3333",
    ".protected/setup.py": "0165781f47c8876d80e97fecc151c34efa94638457367d13b1145f3dcec002d6",
    ".gitignore": "c7e1033b0c9136edc3e77a27d2e5b3e49d1a7f4312162020be316f765e2a069a",
}
EXPECTED_MODES = {
    "claims-availability": 0o755,
    ".protected/claims_seed.sql": 0o644,
    ".protected/audit.key": 0o644,
    "CLAIMS.md": 0o644,
    ".protected/setup.py": 0o755,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {".protected", ".claims-runtime"}
EXPECTED_FILES = {
    ".gitignore",
    ".protected/audit.key",
    ".protected/claims_seed.sql",
    ".protected/setup.py",
    ".protected/verify.py",
    ".claims-runtime/attempts.json",
    ".claims-runtime/attempts.lock",
    ".claims-runtime/audit.jsonl",
    ".claims-runtime/claims.sqlite3",
    ".claims-runtime/initialize.lock",
    ".reference_solution.py",
    "CLAIMS.md",
    "claim-availability.md",
    "claims-availability",
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


def verify_workspace_shape() -> None:
    for path in ROOT.rglob("*"):
        relative_path = path.relative_to(ROOT)
        if relative_path.parts[0] == ".git":
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            fail(f"unexpected scratch artifact or altered path: {relative}")
        if path.is_dir():
            if relative not in EXPECTED_DIRECTORIES:
                fail(f"unexpected scratch directory remains: {relative}")
        elif path.is_file():
            if relative not in EXPECTED_FILES:
                fail(f"unexpected scratch file remains: {relative}")
        else:
            fail(f"unexpected scratch artifact remains: {relative}")


def canonical_connection() -> sqlite3.Connection:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            fail(f"protected claims sandbox input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected claims sandbox input mode changed: {relative}")
    if stat.S_IMODE((PROTECTED / "verify.py").stat().st_mode) != 0o755:
        fail("protected verifier mode changed")
    connection = sqlite3.connect(":memory:")
    connection.executescript(SEED.read_text(encoding="utf-8"))
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    ]


def schema(connection: sqlite3.Connection) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    ]


def expected_results(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    results = []
    for target in TARGETS:
        row = connection.execute(
            "SELECT item_name AS item, office, availability_date AS date, "
            "available, intake_capacity FROM availability "
            "WHERE item_name = ? AND office = ? AND availability_date = ?",
            (target["item"], target["office"], target["date"]),
        ).fetchone()
        if row is None:
            fail("protected seed no longer has a requested availability row")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_deliverable(results: list[dict]) -> None:
    if not DELIVERABLE.is_file():
        fail("claim-availability.md is missing")
    lines = ["| Item | Availability |", "| --- | --- |"]
    for result in results:
        availability = "Available" if result["available"] else "Unavailable"
        lines.append(f"| {result['item']} | {availability} |")
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"claim-availability.md is not UTF-8: {error}")
    if actual != expected:
        fail("claim-availability.md is not the exact required two-row table")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine claims availability executable was not run")
    try:
        actual = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        try:
            integrity = actual.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                fail("claims database integrity check failed")
            if schema(actual) != schema(canonical):
                fail("claims database schema changed")
            for table in DATA_TABLES:
                if rows(actual, table) != rows(canonical, table):
                    fail(f"read-only claims state changed in table {table}")
        finally:
            actual.close()
    except sqlite3.DatabaseError as error:
        fail(f"claims database is unreadable: {error}")


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine claims-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"claims-executable evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one selective retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    unsigned = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail("claims-executable evidence contains a non-object entry")
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("claims-executable evidence has an invalid signature")
        unsigned.append(entry)
    return unsigned


def intervals_overlap(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_attempt_ledger() -> None:
    if not ATTEMPTS.is_file():
        fail("the genuine executable attempt ledger is missing")
    try:
        attempts = json.loads(ATTEMPTS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"the executable attempt ledger is invalid: {error}")
    expected = {
        json.dumps(TARGETS[0], sort_keys=True, ensure_ascii=False): 1,
        json.dumps(TARGETS[1], sort_keys=True, ensure_ascii=False): 2,
    }
    if attempts != expected:
        fail("the successful branch was repeated or the failed branch retry count is wrong")


def verify_process_evidence(entries: list[dict]) -> None:
    for entry in entries:
        for field in (
            "started_ns",
            "finished_ns",
            "process_pid",
            "process_group_id",
            "session_id",
            "parent_pid",
            "attempt",
        ):
            if not isinstance(entry.get(field), int):
                fail(f"execution-evidence field {field} is missing")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an availability operation has an invalid execution interval")
        if entry["finished_ns"] - entry["started_ns"] < 550_000_000:
            fail("an availability operation did not complete its real client interval")
        if not (
            entry["process_pid"] == entry["process_group_id"]
            and entry["process_pid"] == entry["session_id"]
        ):
            fail("each availability check must be a direct, separate Pi Bash tool call")

    process_groups = {entry["process_group_id"] for entry in entries}
    if len(process_groups) != len(entries):
        fail("availability checks reused a shell process session")
    parent = entries[0]["parent_pid"]
    parent_ticks = entries[0].get("parent_start_ticks")
    if parent_ticks == "unavailable" or any(
        entry["parent_pid"] != parent
        or entry.get("parent_start_ticks") != parent_ticks
        for entry in entries
    ):
        fail("availability checks were not issued by one continuing Pi harness run")


def verify_trace(entries: list[dict], results: list[dict]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a forbidden claims operation was used")
    verify_process_evidence(entries)

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
    if first_failure.get("error_code") != "claims_calendar_busy":
        fail("the failed branch did not report the protected temporary class")
    if first_failure.get("retryable") is not True:
        fail("the temporary failure was not explicitly marked retryable")

    if retry.get("success") is not True or retry.get("attempt") != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != first_failure.get("arguments"):
        fail("the retry inputs differ from the failed check")
    if retry.get("result_digest") != digest(results[1]):
        fail("the retry's successful availability evidence is incorrect")

    if not intervals_overlap(retained_success, first_failure):
        fail("the two initial checks did not run in the same parallel tool-call action")
    if retry["started_ns"] <= max(
        retained_success["finished_ns"], first_failure["finished_ns"]
    ):
        fail("the retry began before both initial results had returned")


def main() -> int:
    verify_workspace_shape()
    canonical = canonical_connection()
    try:
        results = expected_results(canonical)
        verify_deliverable(results)
        verify_read_only(canonical)
        verify_attempt_ledger()
        verify_trace(load_audit(), results)
    finally:
        canonical.close()
    print("PASS: exact table and genuine parallel selective-retry trace verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
