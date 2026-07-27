#!/usr/bin/env python3
"""Protected deterministic verifier for the Pi expense dependency task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "__pycache__" / "expenses.sqlite3"
SOURCE = ROOT / ".protected" / "expenses.sql"
AUDIT_KEY = bytes.fromhex(
    (ROOT / ".protected" / "audit.key").read_text(encoding="utf-8").strip()
)
NORTH_LOOP_KEY = ("North Loop mileage claim", "Chicago")
AFTER_SCHOOL_KEY = ("After-school supplies receipt", "Boston")
EVENT_FIELDS = (
    "operation",
    "arguments_json",
    "process_id",
    "parent_process_id",
    "started_ns",
    "finished_ns",
    "success",
    "result_count",
    "sole_id",
    "before_status",
    "after_status",
    "receipt",
    "recipient",
    "message_sha256",
    "error",
)


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def source_records() -> tuple[sqlite3.Row, sqlite3.Row, list[tuple[object, ...]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SOURCE.read_text(encoding="utf-8"))
        requested: list[sqlite3.Row] = []
        for title, city in (NORTH_LOOP_KEY, AFTER_SCHOOL_KEY):
            rows = source.execute(
                """
                SELECT id, title, city, status, amount, currency, expense_date,
                       submitted_by, cost_center
                  FROM expenses
                 WHERE title = ? AND city = ?
                """,
                (title, city),
            ).fetchall()
            require(
                len(rows) == 1 and isinstance(rows[0]["id"], str) and bool(rows[0]["id"]),
                "protected source must resolve each requested expense uniquely",
            )
            requested.append(rows[0])
        require(
            requested[1]["status"] == "submitted",
            "protected conditional branch must begin in submitted status",
        )
        snapshot = [
            tuple(row)
            for row in source.execute(
                """
                SELECT id, title, city, status, amount, currency, expense_date,
                       submitted_by, cost_center
                  FROM expenses
                 ORDER BY id
                """
            )
        ]
        return requested[0], requested[1], snapshot
    finally:
        source.close()


def verify_seals(rows: list[sqlite3.Row]) -> None:
    for row in rows:
        event = {field: row[field] for field in EVENT_FIELDS}
        expected = hmac.new(
            AUDIT_KEY, canonical(event).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        require(hmac.compare_digest(row["seal"], expected), "operation history seal is invalid")


def verify_parallel_phase(rows: list[sqlite3.Row], label: str) -> None:
    require(len(rows) == 2, f"{label} phase must contain exactly two operations")
    require(
        len({row["process_id"] for row in rows}) == 2,
        f"{label} branches were not separate executable processes",
    )
    require(
        len({row["parent_process_id"] for row in rows}) == 1,
        f"{label} branches were not launched together",
    )
    require(
        max(row["started_ns"] for row in rows) <= min(row["finished_ns"] for row in rows),
        f"{label} processes did not overlap",
    )


def verify_execution(
    database: sqlite3.Connection,
    north_loop: sqlite3.Row,
    after_school: sqlite3.Row,
) -> tuple[sqlite3.Row, sqlite3.Row]:
    rows = database.execute("SELECT * FROM audit_log ORDER BY sequence").fetchall()
    require(len(rows) == 7, "expected top-level help and exactly six expense operations")
    require(
        [row["operation"] for row in rows]
        == ["help", "search", "search", "get", "get", "update", "notify"],
        "operations must begin with help, then use two searches, two gets, one update, and one notification",
    )
    require(
        all(row["success"] == 1 and row["error"] is None for row in rows),
        "an expense operation failed or an extra failure was recorded",
    )
    require(
        all(row["started_ns"] <= row["finished_ns"] for row in rows),
        "operation timing evidence is invalid",
    )
    verify_seals(rows)

    help_operation = rows[0]
    require(
        json.loads(help_operation["arguments_json"]) == {},
        "top-level help used unexpected inputs",
    )
    searches = rows[1:3]
    gets = rows[3:5]
    update = rows[5]
    notice = rows[6]
    require(
        help_operation["finished_ns"] < min(row["started_ns"] for row in searches),
        "both searches must wait for top-level help to finish",
    )
    expected_by_key = {
        NORTH_LOOP_KEY: north_loop,
        AFTER_SCHOOL_KEY: after_school,
    }

    observed_keys: set[tuple[str, str]] = set()
    for row in searches:
        arguments = json.loads(row["arguments_json"])
        require(set(arguments) == {"title", "city"}, "search used unexpected inputs")
        key = (arguments["title"], arguments["city"])
        require(key in expected_by_key, "search targeted an out-of-scope expense")
        require(key not in observed_keys, "a requested expense was searched more than once")
        observed_keys.add(key)
        expected = expected_by_key[key]
        require(
            row["result_count"] == 1 and row["sole_id"] == expected["id"],
            "a search did not resolve to its unique stable ID",
        )
    require(observed_keys == set(expected_by_key), "both exact searches are required")
    verify_parallel_phase(searches, "search")

    expected_ids = {str(north_loop["id"]), str(after_school["id"])}
    observed_ids: set[str] = set()
    for row in gets:
        arguments = json.loads(row["arguments_json"])
        require(set(arguments) == {"id"}, "get used unexpected inputs")
        expense_id = arguments["id"]
        require(expense_id in expected_ids, "get did not use a requested search result ID")
        require(expense_id not in observed_ids, "a requested expense was retrieved more than once")
        observed_ids.add(expense_id)
        require(
            row["result_count"] == 1 and row["sole_id"] == expense_id,
            "get did not return its requested complete record",
        )
    require(observed_ids == expected_ids, "both unique expenses must be retrieved")
    verify_parallel_phase(gets, "retrieval")
    require(
        max(row["finished_ns"] for row in searches) < min(row["started_ns"] for row in gets),
        "retrievals must wait for both search results",
    )

    update_arguments = json.loads(update["arguments_json"])
    require(
        update_arguments
        == {
            "id": after_school["id"],
            "from_status": "submitted",
            "to_status": "needs-receipt",
        },
        "the sole mutation was not the permitted conditional receipt change",
    )
    require(
        update["sole_id"] == after_school["id"]
        and update["before_status"] == "submitted"
        and update["after_status"] == "needs-receipt"
        and isinstance(update["receipt"], str)
        and bool(update["receipt"]),
        "the After-school supplies receipt mutation did not succeed as required",
    )
    require(
        max(row["finished_ns"] for row in gets) < update["started_ns"],
        "the mutation must wait for both full-record retrievals",
    )

    notice_arguments = json.loads(notice["arguments_json"])
    require(
        set(notice_arguments) == {"recipient", "receipt", "subject", "message"},
        "notification used unexpected inputs",
    )
    require(
        notice_arguments["recipient"] == "expense review desk"
        and notice_arguments["receipt"] == update["receipt"],
        "notification was not tied to the successful permitted mutation",
    )
    require(
        notice["recipient"] == "expense review desk"
        and notice["receipt"] == update["receipt"]
        and notice["sole_id"] == after_school["id"],
        "notification evidence has the wrong recipient or expense",
    )
    require(
        update["finished_ns"] < notice["started_ns"],
        "expense review desk was notified before the mutation succeeded",
    )
    return update, notice


def verify_state(
    database: sqlite3.Connection,
    north_loop: sqlite3.Row,
    after_school: sqlite3.Row,
    snapshot: list[tuple[object, ...]],
    update: sqlite3.Row,
    notice: sqlite3.Row,
) -> None:
    current = [
        tuple(row)
        for row in database.execute(
            """
            SELECT id, title, city, status, amount, currency, expense_date,
                   submitted_by, cost_center
              FROM expenses
             ORDER BY id
            """
        )
    ]
    expected = [
        tuple(
            "needs-receipt" if index == 3 and row[0] == after_school["id"] else value
            for index, value in enumerate(row)
        )
        for row in snapshot
    ]
    require(current == expected, "expense state contains an out-of-scope or missing change")
    north_loop_now = database.execute(
        "SELECT status FROM expenses WHERE id = ?", (north_loop["id"],)
    ).fetchone()
    require(
        north_loop_now["status"] == north_loop["status"],
        "North Loop mileage claim was changed",
    )

    receipts = database.execute("SELECT * FROM mutation_receipts").fetchall()
    require(len(receipts) == 1, "expected exactly one mutation receipt")
    receipt = receipts[0]
    require(
        receipt["receipt"] == update["receipt"]
        and receipt["expense_id"] == after_school["id"]
        and receipt["from_status"] == "submitted"
        and receipt["to_status"] == "needs-receipt"
        and receipt["consumed"] == 1,
        "mutation receipt does not prove the update-notification dependency",
    )

    notifications = database.execute("SELECT * FROM notifications").fetchall()
    require(len(notifications) == 1, "expected exactly one notification")
    notification = notifications[0]
    require(
        notification["recipient"] == "expense review desk"
        and notification["expense_id"] == after_school["id"]
        and notification["mutation_receipt"] == update["receipt"],
        "notification recipient or mutation dependency is incorrect",
    )
    require(
        notification["subject"].strip() and notification["message"].strip(),
        "expense review notice must contain an outcome",
    )
    outcome = (notification["subject"] + " " + notification["message"]).casefold()
    require(
        "after-school supplies receipt" in outcome and "needs-receipt" in outcome,
        "expense review notice does not accurately identify the outcome",
    )
    require(
        notification["created_ns"] > update["finished_ns"]
        and notification["created_ns"] <= notice["finished_ns"],
        "notification creation time does not follow the successful mutation",
    )
    require(
        notice["message_sha256"]
        == hashlib.sha256(notification["message"].encode("utf-8")).hexdigest(),
        "notification body does not match the audited notice",
    )


def main() -> int:
    try:
        require(DATABASE.is_file(), "the executable expense environment was not used")
        north_loop, after_school, snapshot = source_records()
        database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        database.row_factory = sqlite3.Row
        try:
            update, notice = verify_execution(database, north_loop, after_school)
            verify_state(database, north_loop, after_school, snapshot, update, notice)
        finally:
            database.close()
    except (VerificationError, OSError, sqlite3.Error, json.JSONDecodeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: parallel resolution, scoped conditional mutation, and dependent notice verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
