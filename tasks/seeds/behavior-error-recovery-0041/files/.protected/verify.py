#!/usr/bin/env python3
"""Protected verifier for commit-before-timeout calendar recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".calendar-runtime"
DATABASE = RUNTIME / "calendar.sqlite3"
AUDIT = RUNTIME / "operations.jsonl"
REFERENCE_PROOF = RUNTIME / ".reference-setup-complete"
SEED = PROTECTED / "calendar_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "calendarctl"
DOCUMENTATION = ROOT / "CALENDAR.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGET_ID = "cal-141"
REASON = "request withdrawn by its owner."
EXPECTED_CLIENT_SHA256 = "59bbf804efa6e5c2a790d582669045a0dd9781183996bc4895646d596fac381a"
EXPECTED_SEED_SHA256 = "3d84938bc27d8f65c3cc1bfdea249a4edaa5cef39fcd999e49f84c4c0a73e561"
EXPECTED_KEY_SHA256 = "360731b272c2c51813bf455c2a4d2015d372650f560f555e5339ffcbcaef6bb3"
EXPECTED_DOCUMENTATION_SHA256 = "dca658786765f7f354731dbec3e503d814dc797a6272ab45b8f16670fb8b234c"
EXPECTED_SETUP_SHA256 = "4a208b592517319890a0f0a9251d760c403e364698d542b4947b7d8cd0ccd0a2"
EXPECTED_GITIGNORE_SHA256 = "b5efa84cee63b47b49f3483e9f44090d6a85dadaf4393d555632bc3992f2b59d"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def protected_baseline() -> sqlite3.Connection:
    protected_files = (
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine calendar executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected calendar seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected evidence-signing key"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "operator documentation"),
        (SETUP, EXPECTED_SETUP_SHA256, "protected setup"),
        (GITIGNORE, EXPECTED_GITIGNORE_SHA256, "sandbox ignore rules"),
    )
    for path, expected, label in protected_files:
        if not path.is_file() or sha256(path) != expected:
            fail(f"{label} changed")

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SEED.read_text(encoding="utf-8"))
    return connection


def meeting_rows(connection: sqlite3.Connection) -> dict[str, dict[str, object]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id, title, starts_at, status, close_reason, revision "
        "FROM meetings ORDER BY id"
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}


def expected_state(
    baseline: sqlite3.Connection,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    baseline_rows = meeting_rows(baseline)
    if TARGET_ID not in baseline_rows:
        fail("protected seed does not contain the target meeting")
    original_target = baseline_rows[TARGET_ID]
    if original_target["status"] != "active":
        fail("protected target does not begin active")
    cancelled_target = {
        **original_target,
        "status": "cancelled",
        "close_reason": REASON,
        "revision": int(original_target["revision"]) + 1,
    }
    return baseline_rows, cancelled_target


def verify_database(
    baseline_rows: dict[str, dict[str, object]],
    cancelled_target: dict[str, object],
) -> None:
    if not DATABASE.is_file():
        fail("the genuine calendar executable was not run")
    try:
        actual = sqlite3.connect(DATABASE)
        actual.row_factory = sqlite3.Row
        actual_rows = meeting_rows(actual)
        attempts = [
            tuple(row)
            for row in actual.execute(
                "SELECT meeting_id, attempt_count FROM close_attempts "
                "ORDER BY meeting_id"
            ).fetchall()
        ]
        notifications = [
            tuple(row)
            for row in actual.execute(
                "SELECT notification_id, meeting_id, message FROM notifications "
                "ORDER BY notification_id"
            ).fetchall()
        ]
        tables = {
            row[0]
            for row in actual.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    except (OSError, sqlite3.DatabaseError) as error:
        fail(f"cannot read the runtime database: {error}")
    finally:
        if "actual" in locals():
            actual.close()

    if tables != {"meetings", "close_attempts", "notifications", "sqlite_sequence"}:
        fail("the runtime database schema changed")
    if set(actual_rows) != set(baseline_rows):
        fail("the set of meeting IDs changed")
    for meeting_id, baseline_record in baseline_rows.items():
        if meeting_id != TARGET_ID and actual_rows[meeting_id] != baseline_record:
            fail(f"non-target meeting {meeting_id} was changed")
    if actual_rows[TARGET_ID] != cancelled_target:
        fail("cal-141 is not cancelled with the exact reason and one revision change")
    if attempts != [(TARGET_ID, 1)]:
        fail("cal-141 was closed more than once or another meeting was targeted")
    if notifications:
        fail("a forbidden notification was created")


def load_audit() -> list[dict[str, object]]:
    if not AUDIT.is_file():
        fail("no genuine calendar-executable evidence was recorded")
    try:
        signed_entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        fail(f"calendar-executable evidence is invalid: {error}")
    if len(signed_entries) != 3:
        fail("expected exactly the initial help, close, and direct verification get")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    entries = []
    for signed_entry in signed_entries:
        if not isinstance(signed_entry, dict):
            fail("calendar-executable evidence contains a malformed entry")
        entry = dict(signed_entry)
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("calendar-executable evidence has an invalid signature")
        entries.append(entry)
    return entries


def verify_trace(
    entries: list[dict[str, object]], cancelled_target: dict[str, object]
) -> None:
    help_entry, first, read = entries
    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int):
                fail(f"execution-evidence field {field} is missing")
        if int(entry["started_ns"]) >= int(entry["finished_ns"]):
            fail("an operation has an invalid execution interval")

    if (
        help_entry.get("operation") != "help"
        or help_entry.get("arguments") != {}
        or help_entry.get("success") is not True
        or help_entry.get("outcome") != "usage"
    ):
        fail("the genuine top-level --help was not run first")
    if int(first["started_ns"]) < int(help_entry["finished_ns"]):
        fail("the initial close began before the help invocation completed")

    if first.get("operation") != "close" or first.get("arguments") != {
        "id": TARGET_ID,
        "reason": REASON,
    }:
        fail("the first data operation was not the exact target close")
    if first.get("attempt") != 1 or first.get("success") is not False:
        fail("the first close did not record one uncertain attempt")
    if (
        first.get("outcome") != "timeout_unknown"
        or first.get("error_code") != "deadline_exceeded"
        or first.get("reached_service") is not True
        or first.get("may_have_committed") is not True
        or first.get("committed_digest") != digest(cancelled_target)
    ):
        fail("the required commit-before-timeout condition was not exercised")
    if int(first["finished_ns"]) - int(first["started_ns"]) < 250_000_000:
        fail("the first close did not actually wait for its response deadline")

    if read.get("operation") != "get" or read.get("arguments") != {"id": TARGET_ID}:
        fail("the uncertain close was not followed by a direct get of cal-141")
    if (
        read.get("success") is not True
        or read.get("outcome") != "record"
        or read.get("observed_status") != "cancelled"
        or read.get("result_digest") != digest(cancelled_target)
    ):
        fail("the direct get did not establish that cal-141 was already cancelled")
    if int(read["started_ns"]) < int(first["finished_ns"]):
        fail("the direct get began before the uncertain close returned")
def verify_workspace_shape() -> None:
    allowed = {
        ".calendar-runtime",
        ".git",
        ".gitignore",
        ".protected",
        ".reference_solution.py",
        ".sandbox-home",
        "CALENDAR.md",
        "calendarctl",
    }
    unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    if unexpected:
        fail(f"unexpected sandbox artifacts remain: {', '.join(unexpected)}")
    if RUNTIME.is_dir():
        expected_runtime = {"calendar.sqlite3", "initialize.lock", "operations.jsonl"}
        reference_driver = ROOT / ".reference_solution.py"
        if reference_driver.is_file():
            expected_runtime.add(REFERENCE_PROOF.name)
            try:
                proof = REFERENCE_PROOF.read_text(encoding="utf-8")
            except OSError as error:
                fail(f"reference setup proof is unavailable: {error}")
            if proof != "reference setup completed\n":
                fail("reference setup proof is invalid")
        actual_runtime = {path.name for path in RUNTIME.iterdir()}
        if actual_runtime != expected_runtime:
            fail("unexpected or missing generated calendar runtime artifact")


def main() -> int:
    baseline = protected_baseline()
    try:
        baseline_rows, cancelled_target = expected_state(baseline)
    finally:
        baseline.close()
    verify_workspace_shape()
    verify_database(baseline_rows, cancelled_target)
    verify_trace(load_audit(), cancelled_target)
    print(
        "PASS: cal-141 committed before its timeout and the direct ID check "
        "prevented a duplicate close"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
