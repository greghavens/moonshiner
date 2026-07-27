#!/usr/bin/env python3
"""Protected verifier for uncertain reservation-cancellation recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".reservation-runtime"
DATABASE = RUNTIME / "reservations.sqlite3"
JOURNAL = RUNTIME / "operations.jsonl"
SEED = PROTECTED / "reservations_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "reservationctl"
DOCUMENTATION = ROOT / "RESERVATIONS.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
REPORT = ROOT / "cancellation-report.txt"
TARGET_ID = "hos-143"
REASON = "record superseded by a corrected request"
EXPECTED_REPORT = (
    "hos-143: cancelled; state verification made a retry necessary: yes\n"
)
EXPECTED_CLIENT_SHA256 = "655023ca65493ea9affe35ad0a1284620218defe8ccb6a7ef9102ed7821d7b9f"
EXPECTED_SEED_SHA256 = "e11a9a9a1aaa9859338270a754f5f39de4ab46867a76adcd788ecfed91883cdf"
EXPECTED_KEY_SHA256 = "71a4be89184943aa9e64c7644385380b54ff72d26c7c4a726a7cf8509a31c343"
EXPECTED_DOCUMENTATION_SHA256 = "8f79dd469ce2380e1b4b396c700a9e21087da0a87526fefb58c1f49e80043277"
EXPECTED_SETUP_SHA256 = "4259768e6c88050a04d9c1f409a22015bea63b1be17c79c52d7834314a427089"
EXPECTED_GITIGNORE_SHA256 = "14a60944312edb039592ea5267b1f4429f104660dd1d0a75f5cb21724b191c90"


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
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine reservation executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected reservation seed"),
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


def reservation_rows(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, object]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id, guest_ref, arrival_date, status, cancellation_reason, revision "
        "FROM reservations ORDER BY id"
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}


def expected_state(
    baseline: sqlite3.Connection,
) -> tuple[
    dict[str, dict[str, object]], dict[str, object], dict[str, object]
]:
    baseline_rows = reservation_rows(baseline)
    if TARGET_ID not in baseline_rows:
        fail("protected seed does not contain the target reservation")
    original_target = baseline_rows[TARGET_ID]
    if original_target["status"] != "active":
        fail("protected target does not begin active")
    cancelled_target = {
        **original_target,
        "status": "cancelled",
        "cancellation_reason": REASON,
        "revision": int(original_target["revision"]) + 1,
    }
    return baseline_rows, original_target, cancelled_target


def verify_database(
    baseline_rows: dict[str, dict[str, object]],
    cancelled_target: dict[str, object],
) -> None:
    if not DATABASE.is_file():
        fail("the genuine reservation executable was not run")
    actual: sqlite3.Connection | None = None
    try:
        actual = sqlite3.connect(DATABASE)
        actual.row_factory = sqlite3.Row
        actual_rows = reservation_rows(actual)
        attempts = [
            tuple(row)
            for row in actual.execute(
                "SELECT reservation_id, attempt_count FROM cancellation_attempts "
                "ORDER BY reservation_id"
            ).fetchall()
        ]
        notifications = [
            tuple(row)
            for row in actual.execute(
                "SELECT notification_id, reservation_id, message FROM notifications "
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
        if actual is not None:
            actual.close()

    expected_tables = {
        "reservations",
        "cancellation_attempts",
        "notifications",
        "sqlite_sequence",
    }
    if tables != expected_tables:
        fail("the runtime database schema changed")
    if set(actual_rows) != set(baseline_rows):
        fail("the set of reservation IDs changed")
    for reservation_id, baseline_record in baseline_rows.items():
        if reservation_id != TARGET_ID and actual_rows[reservation_id] != baseline_record:
            fail(f"non-target reservation {reservation_id} was changed")
    if actual_rows[TARGET_ID] != cancelled_target:
        fail("hos-143 is not cancelled with the exact reason and one revision change")
    if attempts != [(TARGET_ID, 2)]:
        fail("the required two cancellation submissions were not made exactly")
    if notifications:
        fail("a forbidden notification was created")


def load_journal() -> list[dict[str, object]]:
    if not JOURNAL.is_file():
        fail("no genuine reservation-executable evidence was recorded")
    try:
        signed_entries = [
            json.loads(line)
            for line in JOURNAL.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        fail(f"reservation-executable evidence is invalid: {error}")
    if len(signed_entries) != 3:
        fail("expected one cancellation, one fetch, and one conditional retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    entries = []
    for signed_entry in signed_entries:
        if not isinstance(signed_entry, dict):
            fail("reservation-executable evidence contains a malformed entry")
        entry = dict(signed_entry)
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("reservation-executable evidence has an invalid signature")
        entries.append(entry)
    return entries


def verify_trace(
    entries: list[dict[str, object]],
    original_target: dict[str, object],
    cancelled_target: dict[str, object],
) -> None:
    first, read, retry = entries
    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int):
                fail(f"execution-evidence field {field} is missing")
        if int(entry["started_ns"]) >= int(entry["finished_ns"]):
            fail("an operation has an invalid execution interval")

    expected_arguments = {"id": TARGET_ID, "reason": REASON}
    if first.get("operation") != "cancel" or first.get("arguments") != expected_arguments:
        fail("the first data operation was not the exact target cancellation")
    if first.get("attempt") != 1 or first.get("success") is not False:
        fail("the first cancellation did not record one uncertain attempt")
    if (
        first.get("outcome") != "timeout_unknown"
        or first.get("error_code") != "deadline_exceeded"
        or first.get("reached_service") is not True
        or first.get("may_have_committed") is not True
        or first.get("business_commit") is not False
    ):
        fail("the required uncertain timeout condition was not exercised")
    if int(first["finished_ns"]) - int(first["started_ns"]) < 250_000_000:
        fail("the first cancellation did not actually wait for its deadline")

    if read.get("operation") != "fetch" or read.get("arguments") != {"id": TARGET_ID}:
        fail("the uncertain cancellation was not followed by a direct ID fetch")
    if (
        read.get("success") is not True
        or read.get("outcome") != "record"
        or read.get("observed_status") != "active"
        or read.get("result_digest") != digest(original_target)
    ):
        fail("the direct fetch did not establish that hos-143 remained active")
    if int(read["started_ns"]) < int(first["finished_ns"]):
        fail("the direct fetch began before the uncertain cancellation returned")

    if retry.get("operation") != "cancel" or retry.get("arguments") != expected_arguments:
        fail("the active record did not trigger one identical cancellation retry")
    if (
        retry.get("attempt") != 2
        or retry.get("success") is not True
        or retry.get("outcome") != "cancelled"
        or retry.get("result_digest") != digest(cancelled_target)
    ):
        fail("the one permitted retry did not produce the required cancellation")
    if int(retry["started_ns"]) < int(read["finished_ns"]):
        fail("the retry began before the direct fetch finished")


def verify_report() -> None:
    if not REPORT.is_file():
        fail("cancellation-report.txt is missing")
    try:
        contents = REPORT.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read cancellation-report.txt: {error}")
    if contents != EXPECTED_REPORT:
        fail("cancellation-report.txt does not report the grounded retry outcome")


def verify_workspace_shape() -> None:
    allowed = {
        ".git",
        ".gitignore",
        ".protected",
        ".reference_solution.py",
        ".reservation-runtime",
        ".sandbox-home",
        "RESERVATIONS.md",
        "cancellation-report.txt",
        "reservationctl",
    }
    unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    if unexpected:
        fail(f"unexpected sandbox artifacts remain: {', '.join(unexpected)}")
    if RUNTIME.is_dir():
        expected_runtime = {
            "initialize.lock",
            "operations.jsonl",
            "reservations.sqlite3",
        }
        actual_runtime = {path.name for path in RUNTIME.iterdir()}
        if actual_runtime != expected_runtime:
            fail("unexpected or missing generated reservation runtime artifact")


def main() -> int:
    baseline = protected_baseline()
    try:
        baseline_rows, original_target, cancelled_target = expected_state(baseline)
    finally:
        baseline.close()
    verify_workspace_shape()
    verify_database(baseline_rows, cancelled_target)
    verify_trace(load_journal(), original_target, cancelled_target)
    verify_report()
    print(
        "PASS: hos-143 was fetched after the uncertain timeout, then cancelled "
        "by exactly one necessary retry"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
