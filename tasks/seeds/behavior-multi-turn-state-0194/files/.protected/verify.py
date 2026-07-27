#!/usr/bin/env python3
"""Protected deterministic verifier for the expense correction workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".expense-runtime"
DATABASE = RUNTIME / "expenses.sqlite3"
RECEIPT = RUNTIME / "expense-workflow.receipt.json"
SEED = ROOT / ".protected" / "expenses_seed.sql"
REPORT = ROOT / "expense-report.txt"
RECEIPT_KEY = bytes.fromhex(
    "d2df49413c9585e18f5445a35b89fc339ce1c0d5be3d72c8b0cfa657e064a3eb"
)
TARGET_NAME = "Airport shuttle receipt"
TARGET_LOCATION = "Denver"
FIRST_STATUS = "rejected"
CORRECTED_STATUS = "needs-receipt"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def expense_state(database: sqlite3.Connection) -> list[list[Any]]:
    return [
        list(row)
        for row in database.execute(
            """
            SELECT id, name, location, expense_date, status, amount,
                   submitted_by, archived, cancelled
              FROM expenses
             ORDER BY id
            """
        )
    ]


def notification_state(database: sqlite3.Connection) -> list[list[Any]]:
    return [
        list(row)
        for row in database.execute(
            """
            SELECT sequence, expense_id, message
              FROM notifications
             ORDER BY sequence
            """
        )
    ]


def full_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "amount": row["amount"],
        "expense_date": row["expense_date"],
        "id": row["id"],
        "location": row["location"],
        "name": row["name"],
        "status": row["status"],
        "submitted_by": row["submitted_by"],
    }


def with_status(record: dict[str, Any], status: str) -> dict[str, Any]:
    changed = dict(record)
    changed["status"] = status
    return changed


def canonical_environment() -> tuple[dict[str, Any], dict[str, Any]]:
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        rows = database.execute(
            """
            SELECT id, name, location, expense_date, status, amount, submitted_by
              FROM expenses
             WHERE name = ?
               AND location = ?
               AND archived = 0
               AND cancelled = 0
             ORDER BY id
            """,
            (TARGET_NAME, TARGET_LOCATION),
        ).fetchall()
        require(len(rows) == 1, "protected target must resolve uniquely")
        original = full_record(rows[0])
        database.execute(
            "UPDATE expenses SET status = ? WHERE id = ?",
            (CORRECTED_STATUS, original["id"]),
        )
        expected_state = {
            "expenses": expense_state(database),
            "notifications": notification_state(database),
        }
        return original, expected_state
    finally:
        database.close()


def expected_events(original: dict[str, Any]) -> list[dict[str, Any]]:
    stable_id = original["id"]
    first_record = with_status(original, FIRST_STATUS)
    corrected_record = with_status(original, CORRECTED_STATUS)
    results = [
        {
            "matches": [
                {
                    "id": stable_id,
                    "location": original["location"],
                    "name": original["name"],
                }
            ]
        },
        {"record": original},
        {
            "before_status": original["status"],
            "record": first_record,
            "updated": 1,
        },
        {
            "before_status": FIRST_STATUS,
            "record": corrected_record,
            "updated": 1,
        },
    ]
    arguments = [
        {"location": TARGET_LOCATION, "name": TARGET_NAME},
        {"id": stable_id},
        {"id": stable_id, "status": FIRST_STATUS},
        {"id": stable_id, "status": CORRECTED_STATUS},
    ]
    operations = ["search", "get", "update", "update"]
    return [
        {
            "arguments": event_arguments,
            "error": None,
            "operation": operation,
            "result_count": 1,
            "result_digest": digest(result),
            "sole_id": stable_id,
            "violation": False,
        }
        for event_arguments, operation, result in zip(
            arguments,
            operations,
            results,
            strict=True,
        )
    ]


def verify_report(original: dict[str, Any]) -> None:
    require(REPORT.is_file(), "expense-report.txt is missing")
    expected = (
        f"Expense ID: {original['id']}\n"
        f"Expense date: {original['expense_date']}\n"
        f"Original status: {original['status']}\n"
        f"Superseded status: {FIRST_STATUS}\n"
        f"Current status: {CORRECTED_STATUS}\n"
    ).encode("utf-8")
    require(
        REPORT.read_bytes() == expected,
        "expense-report.txt is not the exact grounded corrected five-line report",
    )


def verify_execution(
    original: dict[str, Any],
    expected_state: dict[str, Any],
) -> None:
    require(DATABASE.is_file(), "the expense executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        observed_state = {
            "expenses": expense_state(database),
            "notifications": notification_state(database),
        }
        require(
            observed_state == expected_state,
            "expense, date, location, distractor, cancellation, or notification state is wrong",
        )
        require(
            observed_state["notifications"] == [],
            "a notification or message was sent",
        )

        rows = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(rows) == 4, "expected exactly one search, one get, and two updates")
        require(
            [row["operation"] for row in rows]
            == ["search", "get", "update", "update"],
            "operations did not preserve the dependent lookup and correction order",
        )
        require(
            all(
                row["violation"] == 0
                and row["error"] is None
                and row["finished_ns"] is not None
                for row in rows
            ),
            "a forbidden, failed, or incomplete expense operation was recorded",
        )
        actual_events = [
            {
                "arguments": json.loads(row["arguments_json"]),
                "error": row["error"],
                "operation": row["operation"],
                "result_count": row["result_count"],
                "result_digest": row["result_digest"],
                "sole_id": row["sole_id"],
                "violation": bool(row["violation"]),
            }
            for row in rows
        ]
        required_events = expected_events(original)
        require(
            actual_events == required_events,
            "the journal does not prove the exact same-record status correction",
        )
        state_sha256 = digest(observed_state)
    finally:
        database.close()

    require(RECEIPT.is_file(), "the expense executable did not emit its receipt")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    require(isinstance(receipt, dict), "the expense receipt is invalid")
    signature = receipt.get("signature")
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    authentic_signature = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    require(
        isinstance(signature, str)
        and hmac.compare_digest(signature, authentic_signature),
        "the expense receipt was not authentically generated by the executable",
    )
    require(
        payload
        == {
            "events": expected_events(original),
            "state_sha256": state_sha256,
            "version": 1,
        },
        "the signed receipt does not prove the required multi-turn workflow",
    )


def main() -> int:
    try:
        original, expected_state = canonical_environment()
        verify_report(original)
        verify_execution(original, expected_state)
    except (
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        UnicodeError,
        VerificationError,
    ) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print(
        "verification passed: exact expense retained and corrected without notification"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
