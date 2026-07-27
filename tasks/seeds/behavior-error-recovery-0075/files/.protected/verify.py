#!/usr/bin/env python3
"""Protected deterministic checks for uncertain appointment cancellation recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".appointments-runtime" / "appointments.sqlite3"
KEY_PATH = ROOT / ".protected" / "audit.key"
RESULT_PATH = ROOT / "cancellation-result.txt"
TARGET_ID = "hea-175"
TARGET_APPOINTMENT = "Dental cleaning — Morgan Lee"
DISTRACTOR_ID = "hea-715"
REASON = "no longer needed for the scheduled work."
KEY_SHA256 = "fc1d85f6c878cfe8f641763311cd93445c1a396c56cbe36f371ca9220311b6e4"
FIXTURE_SHA256 = {
    ".gitignore": "d577712cb077034e964181b3eb19218a10735593fc644e2c942c65298abf3860",
    ".protected/setup.py": "36c479def74c7a465ceab2447e55b13bb7d81a6896e4192a4eb823598d902730",
    ".protected/appointments_seed.sql": "5fad3af86262a4054838c449abce5638b03671f85d84de615c12c7b3e30ffc07",
    ".appointments/service.py": "095737695388364bccc364bd685cb00acf32e19cabf3cc43437898a0bfd05aaf",
    "APPOINTMENTS.md": "1df67235f385d102ef8d2ef71c9a4be667b75dbaeb44051b9ee88be5e6989641",
    "appointmentctl": "ad1f912a1064664d9e17971e89edba904c46a08fa4b75805908f61f01b22c540",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"required fixture is unreadable: {path.relative_to(ROOT)}: {error}")


def verify_fixtures() -> bytes:
    for relative, expected in FIXTURE_SHA256.items():
        if sha256(ROOT / relative) != expected:
            fail(f"fixture changed: {relative}")
    try:
        key_bytes = KEY_PATH.read_bytes()
    except OSError as error:
        fail(f"protected audit key is unreadable: {error}")
    if hashlib.sha256(key_bytes).hexdigest() != KEY_SHA256:
        fail("protected audit key changed")
    return key_bytes.strip()


def decode_event(
    row: sqlite3.Row, expected_sequence: int, key: bytes
) -> dict[str, Any]:
    if row["sequence"] != expected_sequence:
        fail("operation audit sequence is not contiguous")
    payload = row["payload"]
    seal = row["seal"]
    if not isinstance(payload, str) or not isinstance(seal, str):
        fail("operation audit entry is malformed")
    expected_seal = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(seal, expected_seal):
        fail(f"operation audit entry {expected_sequence} is not service-signed")
    try:
        event: Any = json.loads(payload)
    except json.JSONDecodeError as error:
        fail(f"operation audit entry {expected_sequence} is invalid: {error}")
    if not isinstance(event, dict) or canonical(event).decode("utf-8") != payload:
        fail(f"operation audit entry {expected_sequence} is not canonical")
    return event


def main() -> int:
    key = verify_fixtures()
    try:
        database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        database.row_factory = sqlite3.Row
    except sqlite3.Error as error:
        fail(f"appointment database is unavailable: {error}")

    try:
        tables = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            if not row[0].startswith("sqlite_")
        }
        if tables != {"appointments", "settings", "audit_events"}:
            fail("appointment database schema changed")

        appointments = {
            row["id"]: dict(row)
            for row in database.execute(
                "SELECT id, appointment, scheduled_for, clinic, status, "
                "cancellation_reason FROM appointments ORDER BY id"
            )
        }
        if appointments != {
            TARGET_ID: {
                "id": TARGET_ID,
                "appointment": TARGET_APPOINTMENT,
                "scheduled_for": "2026-08-14T09:30:00-06:00",
                "clinic": "North Dental Clinic",
                "status": "cancelled",
                "cancellation_reason": REASON,
            },
            DISTRACTOR_ID: {
                "id": DISTRACTOR_ID,
                "appointment": "Dental cleaning — Morgan Lee follow-up",
                "scheduled_for": "2026-08-28T09:30:00-06:00",
                "clinic": "North Dental Clinic",
                "status": "active",
                "cancellation_reason": None,
            },
            "hea-246": {
                "id": "hea-246",
                "appointment": "Vision exam — Taylor Brooks",
                "scheduled_for": "2026-08-18T13:00:00-06:00",
                "clinic": "Central Eye Clinic",
                "status": "active",
                "cancellation_reason": None,
            },
        }:
            fail("appointment state does not match the exact requested scope")

        settings = [
            tuple(row)
            for row in database.execute(
                "SELECT name, value FROM settings ORDER BY name"
            )
        ]
        if settings != [("first_cancel_timeout_consumed", "1")]:
            fail("the uncertain-response fault path was not exercised exactly once")

        audit_rows = list(
            database.execute(
                "SELECT sequence, payload, seal FROM audit_events ORDER BY sequence"
            )
        )
    except sqlite3.Error as error:
        fail(f"appointment database could not be verified: {error}")
    finally:
        database.close()

    if len(audit_rows) != 2:
        fail("expected one cancellation followed by one exact-ID retrieval")
    events = [
        decode_event(row, index, key) for index, row in enumerate(audit_rows, 1)
    ]
    if events[0] != {
        "sequence": 1,
        "operation": "cancel",
        "appointment_id": TARGET_ID,
        "reason": REASON,
        "status_before": "active",
        "status_after": "cancelled",
    }:
        fail("first appointment operation was not the required single cancellation")
    if events[1] != {
        "sequence": 2,
        "operation": "get",
        "appointment_id": TARGET_ID,
        "observed_status": "cancelled",
    }:
        fail("the uncertain commit was not resolved by an exact target-ID retrieval")

    try:
        result = RESULT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"cancellation-result.txt is missing or unreadable: {error}")
    expected_result = (
        f"Appointment: {TARGET_APPOINTMENT} ({TARGET_ID})\n"
        "Verified final status: cancelled\n"
        "Retry sent: no\n"
    )
    if result != expected_result:
        fail("deliverable is not the exact grounded three-line result")

    allowed_entries = {
        ".git",
        ".gitignore",
        ".appointments",
        ".appointments-runtime",
        ".protected",
        ".reference_solution.py",
        "APPOINTMENTS.md",
        "appointmentctl",
        "cancellation-result.txt",
    }
    unexpected = {path.name for path in ROOT.iterdir()} - allowed_entries
    if unexpected:
        fail("unexpected scratch artifacts exist: " + ", ".join(sorted(unexpected)))

    try:
        runtime_entries = {
            path.name for path in (ROOT / ".appointments-runtime").iterdir()
        }
    except OSError as error:
        fail(f"appointment runtime is unavailable: {error}")
    if runtime_entries != {"appointments.sqlite3"}:
        fail("appointment runtime contains unexpected files")

    print(
        "PASS: the cancellation committed before a genuine client timeout; "
        "the exact-ID retrieval resolved the outcome without a retry"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
