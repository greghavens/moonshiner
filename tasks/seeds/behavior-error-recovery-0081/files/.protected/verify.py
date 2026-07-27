#!/usr/bin/env python3
"""Protected verification for uncertain-commit calendar cancellation recovery."""

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
RUNTIME = ROOT / ".calendar-runtime"
DATABASE = RUNTIME / "calendar.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
ATTEMPTS = RUNTIME / "attempts.json"
DELIVERABLE = ROOT / "cancellation-report.md"
SEED = PROTECTED / "calendar_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "calendarctl"
DOCUMENTATION = ROOT / "CALENDAR.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGET_ID = "cal-181"
TARGET_NAME = "Museum Partnership Briefing"
TARGET_REASON = "no longer needed for the scheduled work."
DISTRACTOR_ID = "cal-581"
EXPECTED_CLIENT_SHA256 = "8df57401f71cbed68635bfb0ce12405ce986fcf6aeb751dc76d8072d4edde560"
EXPECTED_SEED_SHA256 = "aec451801f8331a133d435b96135adf0a26cdf756001b9b9a2b6bc82fa8a26c2"
EXPECTED_KEY_SHA256 = "8a23a781e063b65a23170b4eaba7cf07e8e99b35a27272fa8407c179d81ec0a8"
EXPECTED_DOCUMENTATION_SHA256 = "7eb3b2e37f7c2d9e140438f16091e1af8eb1656142ff7082d285a6db9fcd6e38"
EXPECTED_SETUP_SHA256 = "61519eb058471a9745aa60e5d2a0c4df959744f5e2ed104acc4115138ecc5a5c"
EXPECTED_GITIGNORE_SHA256 = "7b2108e84150952df90e87620dab75cffd4baa69187b179f32df4d1a07bae03b"
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".protected",
    ".calendar-runtime",
    ".reference_solution.py",
    "CALENDAR.md",
    "calendarctl",
    "cancellation-report.md",
}
EXPECTED_PROTECTED_ENTRIES = {
    "audit.key",
    "calendar_seed.sql",
    "setup.py",
    "verify.py",
}
EXPECTED_RUNTIME_ENTRIES = {
    "attempts.json",
    "attempts.lock",
    "audit.jsonl",
    "calendar.sqlite3",
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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine calendar executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected calendar seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected signing material"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "calendar documentation"),
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
        fail(f"cannot load protected calendar seed: {error}")
    return connection


def schema(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    ]


def meeting_rows(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT id, name, location, scheduled_at, status, "
            "cancellation_reason, cancel_count FROM meetings ORDER BY id"
        ).fetchall()
    ]


def expected_final_rows(canonical: sqlite3.Connection) -> list[tuple[Any, ...]]:
    rows = meeting_rows(canonical)
    expected: list[tuple[Any, ...]] = []
    found = False
    for row in rows:
        if row[0] == TARGET_ID:
            found = True
            expected.append((*row[:4], "cancelled", TARGET_REASON, 1))
        else:
            expected.append(row)
    if not found:
        fail("the protected seed lacks the requested meeting")
    return expected


def expected_get_result(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT id, name, location, scheduled_at, status, cancellation_reason "
        "FROM meetings WHERE id = ?",
        (TARGET_ID,),
    ).fetchone()
    if row is None:
        fail("the resulting database lacks the requested meeting")
    return {
        "id": row["id"],
        "name": row["name"],
        "location": row["location"],
        "scheduled_at": row["scheduled_at"],
        "status": row["status"],
        "cancellation_reason": row["cancellation_reason"],
    }


def verify_state(canonical: sqlite3.Connection) -> dict[str, Any]:
    if not DATABASE.is_file():
        fail("the genuine calendar executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            fail("the calendar database failed its integrity check")
        if schema(actual) != schema(canonical):
            fail("the calendar database schema changed")
        if meeting_rows(actual) != expected_final_rows(canonical):
            fail("calendar state does not reflect exactly one target cancellation")
        distractor = actual.execute(
            "SELECT status, cancellation_reason, cancel_count "
            "FROM meetings WHERE id = ?",
            (DISTRACTOR_ID,),
        ).fetchone()
        if distractor != ("active", None, 0):
            fail("the similarly named distractor was changed")
        return expected_get_result(actual)
    finally:
        actual.close()


def verify_deliverable(result: dict[str, Any]) -> None:
    if not DELIVERABLE.is_file():
        fail("cancellation-report.md is missing")
    expected = (
        f"- Meeting: {result['name']} ({result['id']})\n"
        f"- Verified status: {result['status']}\n"
        "- Retry performed: no\n"
    )
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read cancellation-report.md as UTF-8: {error}")
    if actual != expected:
        fail("cancellation-report.md is not the exact grounded final report")
    if len(actual.splitlines()) != 3 or any(
        not line.startswith("- ") for line in actual.splitlines()
    ):
        fail("cancellation-report.md is not exactly three Markdown bullets")
    forbidden = (
        TARGET_REASON,
        "request_timed_out",
        "commit_state",
        "West Gallery",
        "2026-09-17",
        DISTRACTOR_ID,
    )
    if any(value in actual for value in forbidden):
        fail("cancellation-report.md contains information outside its contract")


def load_audit() -> list[dict[str, Any]]:
    if not AUDIT.is_file():
        fail("no signed calendar execution evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"calendar operation evidence is invalid: {error}")
    if len(entries) != 2:
        fail("expected one cancellation followed by one exact-ID retrieval")
    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    verified: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail("an operation evidence entry is not an object")
        unsigned = dict(entry)
        signature = unsigned.pop("signature", None)
        expected = hmac.new(key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("calendar operation evidence was not emitted intact")
        verified.append(unsigned)
    return verified


def verify_trace(entries: list[dict[str, Any]], result: dict[str, Any]) -> None:
    cancel, get = entries
    for entry in entries:
        for field in ("attempt", "started_ns", "finished_ns", "pid"):
            if not isinstance(entry.get(field), int) or isinstance(
                entry.get(field), bool
            ):
                fail(f"signed execution field {field} is missing or invalid")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an operation has an invalid execution interval")

    if cancel.get("operation") != "cancel" or cancel.get("arguments") != {
        "id": TARGET_ID,
        "reason": TARGET_REASON,
    }:
        fail("the first calendar operation was not the exact target cancellation")
    if cancel.get("attempt") != 1 or cancel.get("success") is not False:
        fail("the target did not have exactly one uncertain initial cancellation")
    if (
        cancel.get("error_code") != "request_timed_out"
        or cancel.get("commit_state") != "unknown"
        or cancel.get("changed") is not True
    ):
        fail("the initial mutation lacks intact uncertain-timeout evidence")

    if get.get("operation") != "get" or get.get("arguments") != {"id": TARGET_ID}:
        fail("the timeout was not resolved with one exact-ID retrieval")
    if get.get("attempt") != 1 or get.get("success") is not True:
        fail("the exact-ID verification did not succeed once")
    if get.get("result_digest") != digest(result):
        fail("the reported final state was not returned by exact-ID retrieval")
    if get["started_ns"] <= cancel["finished_ns"]:
        fail("the exact-ID retrieval did not occur after cancellation returned")
    if get["pid"] == cancel["pid"]:
        fail("the cancellation and retrieval were not direct executable invocations")


def verify_attempts() -> None:
    try:
        attempts = json.loads(ATTEMPTS.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"operation-attempt state is invalid: {error}")
    if not isinstance(attempts, dict) or sorted(attempts.values()) != [1, 1]:
        fail("operation-attempt state shows a repeated or missing invocation")


def verify_layout() -> None:
    root_entries = {path.name for path in ROOT.iterdir()}
    unexpected = sorted(root_entries - ALLOWED_ROOT_ENTRIES)
    if unexpected:
        fail(f"unexpected scratch artifact remains: {unexpected[0]}")
    if {path.name for path in PROTECTED.iterdir()} != EXPECTED_PROTECTED_ENTRIES:
        fail("the protected file inventory changed")
    if not RUNTIME.is_dir():
        fail("the genuine calendar runtime directory is missing")
    if {path.name for path in RUNTIME.iterdir()} != EXPECTED_RUNTIME_ENTRIES:
        fail("the calendar runtime has missing or unexpected artifacts")


def main() -> int:
    canonical = canonical_connection()
    try:
        result = verify_state(canonical)
        verify_deliverable(result)
        verify_trace(load_audit(), result)
        verify_attempts()
        verify_layout()
    finally:
        canonical.close()
    print(
        "verification passed: one uncertain cancellation, exact-ID recovery, "
        "no duplicate mutation, untouched distractor, and grounded report verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
