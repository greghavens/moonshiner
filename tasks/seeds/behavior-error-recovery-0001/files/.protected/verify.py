#!/usr/bin/env python3
"""Protected deterministic verifier for the calendar error-recovery task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".calendar-runtime" / "calendar.sqlite3"
SEED = ROOT / ".protected" / "calendar_seed.sql"
KEY_PATH = ROOT / ".protected" / "receipt.key"
REPORT = ROOT / "cancellation-result.txt"
RECEIPT = ROOT / "cancellation.receipt.json"
PROTECTED_HASHES = {
    ".gitignore": "b6debf674a9758027e9f05eaab99205a550914bc88c318c29f4349cb61ecd618",
    ".protected/calendar_seed.sql": "d4dcad7e6315eca8081cfd18909b96db1b68ee0fbaba705ffe918ef1560904fa",
    ".protected/receipt.key": "b8af15ce04b6a2cab920a1f7057f761a0c3eb5b664f565633fa646c158b78e7c",
    ".protected/setup.py": "8f2e961a3f94a13bcd65e56ca0a2bba3587dbd5b0495aba0458ea8289ccad043",
    "CALENDAR.md": "08ffbe4ef5ffef8255d707981971e42be2d1802d85dfc0f9405f794dc666eff6",
    "calendarctl": "8e6a17e2f2db9b6faa2596d51ee43af3a4352410d0f0de8e4f7bf0452344d266",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def full_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "location": row["location"],
        "notes": row["notes"],
        "organizer": row["organizer"],
        "starts_at": row["starts_at"],
        "status": row["status"],
        "title": row["title"],
    }


def calendar_state(database: sqlite3.Connection) -> dict[str, list[list[Any]]]:
    return {
        "availability": [
            list(row)
            for row in database.execute(
                """
                SELECT location, calendar_date, open_slots
                  FROM availability
                 ORDER BY location, calendar_date
                """
            )
        ],
        "meetings": [
            list(row)
            for row in database.execute(
                """
                SELECT id, title, location, starts_at, status, organizer, notes
                  FROM meetings
                 ORDER BY id
                """
            )
        ],
        "notifications": [
            list(row)
            for row in database.execute(
                """
                SELECT sequence, meeting_id, recipient, message
                  FROM notifications
                 ORDER BY sequence
                """
            )
        ],
        "preferences": [
            list(row)
            for row in database.execute(
                "SELECT owner, preference FROM saved_preferences ORDER BY owner"
            )
        ],
    }


def canonical_expectations() -> tuple[dict[str, Any], dict[str, list[list[Any]]]]:
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        cursor = database.execute(
            "UPDATE meetings SET status = 'cancelled' WHERE id = 'cal-271'"
        )
        require(cursor.rowcount == 1, "protected target meeting is missing")
        row = database.execute(
            """
            SELECT id, title, location, starts_at, status, organizer, notes
              FROM meetings
             WHERE id = 'cal-271'
            """
        ).fetchone()
        require(row is not None, "protected target meeting cannot be retrieved")
        return full_record(row), calendar_state(database)
    finally:
        database.close()


def verify_protected_files() -> None:
    for relative, expected_hash in PROTECTED_HASHES.items():
        path = ROOT / relative
        require(path.is_file(), f"protected task file is missing: {relative}")
        require(
            file_sha256(path) == expected_hash,
            f"protected task file changed: {relative}",
        )


def verify_report(record: dict[str, Any]) -> None:
    require(REPORT.is_file(), "cancellation-result.txt is missing")
    expected = (
        f"{record['id']}'s verified status is {record['status']}.\n"
        "One cancellation attempt was made and no notification was sent.\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "cancellation-result.txt is not the exact two-sentence grounded result",
    )


def expected_receipt(
    record: dict[str, Any],
    state: dict[str, list[list[Any]]],
) -> dict[str, Any]:
    payload = {
        "events": [
            {
                "arguments": {"id": "cal-271"},
                "committed": True,
                "error_kind": "deadline_exceeded",
                "operation": "cancel",
            },
            {
                "arguments": {"id": "cal-271"},
                "operation": "get",
                "result_digest": digest({"record": record}),
            },
        ],
        "state_sha256": digest(state),
        "version": 1,
    }
    key = bytes.fromhex(KEY_PATH.read_text(encoding="utf-8").strip())
    receipt = dict(payload)
    receipt["signature"] = hmac.new(
        key,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return receipt


def verify_receipt(
    record: dict[str, Any],
    state: dict[str, list[list[Any]]],
) -> None:
    require(RECEIPT.is_file(), "the executable did not issue its protected receipt")
    try:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"the executable receipt is invalid: {error}") from error
    require(isinstance(receipt, dict), "the executable receipt must be a JSON object")
    signature = receipt.get("signature")
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    key = bytes.fromhex(KEY_PATH.read_text(encoding="utf-8").strip())
    authentic = hmac.new(
        key,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, authentic),
        "the calendar receipt was not authentically generated by the executable",
    )
    require(
        receipt == expected_receipt(record, state),
        "the receipt does not prove the exact cancel-then-verify workflow",
    )


def verify_execution(
    record: dict[str, Any],
    expected_state: dict[str, list[list[Any]]],
) -> None:
    require(DATABASE.is_file(), "the genuine calendar environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 2, "expected exactly one cancellation and one retrieval")
        require(
            [event["operation"] for event in events] == ["cancel", "get"],
            "calendar operations must be one cancellation followed by one retrieval",
        )
        cancellation, retrieval = events
        require(
            json.loads(cancellation["arguments_json"]) == {"id": "cal-271"},
            "the cancellation did not use only the supplied stable ID",
        )
        require(
            cancellation["finished_ns"] is not None
            and int(cancellation["committed"]) == 1
            and cancellation["result_digest"] is None
            and cancellation["error_kind"] == "deadline_exceeded",
            "the first operation was not the genuine uncertain-commit deadline path",
        )
        require(
            json.loads(retrieval["arguments_json"]) == {"id": "cal-271"},
            "the verification retrieval did not use only cal-271",
        )
        require(
            retrieval["finished_ns"] is not None
            and int(retrieval["committed"]) == 0
            and retrieval["error_kind"] is None
            and retrieval["result_digest"] == digest({"record": record}),
            "the retrieval did not return the complete post-cancellation target record",
        )
        require(
            int(cancellation["finished_ns"]) < int(retrieval["started_ns"]),
            "cal-271 was retrieved before the cancellation outcome became uncertain",
        )
        require(
            calendar_state(database) == expected_state,
            "meeting, preference, availability, or notification state is out of scope",
        )
    finally:
        database.close()


def main() -> int:
    try:
        verify_protected_files()
        record, expected_state = canonical_expectations()
        verify_report(record)
        verify_execution(record, expected_state)
        verify_receipt(record, expected_state)
    except (VerificationError, OSError, sqlite3.Error, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: one uncertain cancellation was verified by ID with no retry or notification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
