#!/usr/bin/env python3
"""Protected deterministic verifier for the Pi recruiting dependency task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "__pycache__" / "recruiting.sqlite3"
SOURCE = ROOT / ".protected" / "recruiting.sql"
AUDIT_KEY = bytes.fromhex(
    (ROOT / ".protected" / "audit.key").read_text(encoding="utf-8").strip()
)
NOELLE_KEY = ("Noelle Martin", "Analytics")
RAVI_KEY = ("Ravi Patel", "Customer Success")
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
        for name, location in (NOELLE_KEY, RAVI_KEY):
            rows = source.execute(
                """
                SELECT id, name, location, status, role, owner
                  FROM candidates
                 WHERE name = ? AND location = ?
                """,
                (name, location),
            ).fetchall()
            require(
                len(rows) == 1 and isinstance(rows[0]["id"], str) and bool(rows[0]["id"]),
                "protected source must resolve each requested candidate uniquely",
            )
            requested.append(rows[0])
        snapshot = [
            tuple(row)
            for row in source.execute(
                "SELECT id, name, location, status, role, owner FROM candidates ORDER BY id"
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
        f"{label} branches were not launched by one Bash-tool action",
    )
    require(
        max(row["started_ns"] for row in rows) <= min(row["finished_ns"] for row in rows),
        f"{label} processes did not overlap",
    )


def verify_execution(
    database: sqlite3.Connection,
    noelle: sqlite3.Row,
    ravi: sqlite3.Row,
) -> tuple[sqlite3.Row, sqlite3.Row]:
    rows = database.execute("SELECT * FROM audit_log ORDER BY sequence").fetchall()
    require(len(rows) == 7, "expected one help call and exactly six recruiting operations")
    require(
        [row["operation"] for row in rows]
        == ["help", "search", "search", "get", "get", "update", "notify"],
        "operations must be help, two searches, two gets, one update, then one notification",
    )
    require(
        all(row["success"] == 1 and row["error"] is None for row in rows),
        "a recruiting operation failed or an extra failure was recorded",
    )
    require(
        all(row["started_ns"] <= row["finished_ns"] for row in rows),
        "operation timing evidence is invalid",
    )
    verify_seals(rows)

    help_call = rows[0]
    searches = rows[1:3]
    gets = rows[3:5]
    update = rows[5]
    notice = rows[6]
    expected_by_key = {NOELLE_KEY: noelle, RAVI_KEY: ravi}

    require(
        json.loads(help_call["arguments_json"]) == {}
        and help_call["result_count"] is None
        and help_call["sole_id"] is None,
        "the initial operation was not the executable's root help",
    )
    require(
        help_call["finished_ns"] < min(row["started_ns"] for row in searches),
        "the exact searches must start only after help finishes",
    )

    observed_keys: set[tuple[str, str]] = set()
    for row in searches:
        arguments = json.loads(row["arguments_json"])
        require(set(arguments) == {"name", "location"}, "search used unexpected inputs")
        key = (arguments["name"], arguments["location"])
        require(key in expected_by_key, "search targeted an out-of-scope candidate")
        require(key not in observed_keys, "a requested candidate was searched more than once")
        observed_keys.add(key)
        expected = expected_by_key[key]
        require(
            row["result_count"] == 1 and row["sole_id"] == expected["id"],
            "a search did not resolve to its unique stable ID",
        )
    require(observed_keys == set(expected_by_key), "both exact searches are required")
    verify_parallel_phase(searches, "search")

    expected_ids = {str(noelle["id"]), str(ravi["id"])}
    observed_ids: set[str] = set()
    for row in gets:
        arguments = json.loads(row["arguments_json"])
        require(set(arguments) == {"id"}, "get used unexpected inputs")
        record_id = arguments["id"]
        require(record_id in expected_ids, "get did not use a requested search result ID")
        require(record_id not in observed_ids, "a requested candidate was retrieved more than once")
        observed_ids.add(record_id)
        require(
            row["result_count"] == 1 and row["sole_id"] == record_id,
            "get did not return its requested complete record",
        )
    require(observed_ids == expected_ids, "both unique candidates must be retrieved")
    verify_parallel_phase(gets, "retrieval")
    require(
        max(row["finished_ns"] for row in searches) < min(row["started_ns"] for row in gets),
        "retrievals must wait for both search results",
    )

    update_arguments = json.loads(update["arguments_json"])
    require(
        update_arguments
        == {"id": ravi["id"], "from_status": "screening", "to_status": "offer-review"},
        "the sole mutation was not the permitted conditional Ravi Patel change",
    )
    require(
        update["sole_id"] == ravi["id"]
        and update["before_status"] == "screening"
        and update["after_status"] == "offer-review"
        and isinstance(update["receipt"], str)
        and bool(update["receipt"]),
        "the Ravi Patel mutation did not succeed with the required status transition",
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
        notice_arguments["recipient"] == "talent operations"
        and notice_arguments["receipt"] == update["receipt"],
        "notification was not scoped to talent operations and the successful mutation",
    )
    require(
        notice["recipient"] == "talent operations"
        and notice["receipt"] == update["receipt"]
        and notice["sole_id"] == ravi["id"],
        "notification evidence does not depend on the Ravi Patel mutation",
    )
    require(
        update["finished_ns"] < notice["started_ns"],
        "talent operations was notified before the mutation succeeded",
    )
    return update, notice


def verify_state(
    database: sqlite3.Connection,
    noelle: sqlite3.Row,
    ravi: sqlite3.Row,
    snapshot: list[tuple[object, ...]],
    update: sqlite3.Row,
    notice: sqlite3.Row,
) -> None:
    current = [
        tuple(row)
        for row in database.execute(
            "SELECT id, name, location, status, role, owner FROM candidates ORDER BY id"
        )
    ]
    expected = [
        tuple("offer-review" if index == 3 and row[0] == ravi["id"] else value for index, value in enumerate(row))
        for row in snapshot
    ]
    require(current == expected, "candidate state contains an out-of-scope or missing change")
    noelle_now = database.execute(
        "SELECT status FROM candidates WHERE id = ?", (noelle["id"],)
    ).fetchone()
    require(noelle_now["status"] == noelle["status"], "Noelle Martin was changed")

    receipts = database.execute("SELECT * FROM mutation_receipts").fetchall()
    require(len(receipts) == 1, "expected exactly one mutation receipt")
    receipt = receipts[0]
    require(
        receipt["receipt"] == update["receipt"]
        and receipt["record_id"] == ravi["id"]
        and receipt["from_status"] == "screening"
        and receipt["to_status"] == "offer-review"
        and receipt["consumed"] == 1,
        "mutation receipt does not prove the permitted update-notification dependency",
    )

    notifications = database.execute("SELECT * FROM notifications").fetchall()
    require(len(notifications) == 1, "expected exactly one notification")
    notification = notifications[0]
    require(
        notification["recipient"] == "talent operations"
        and notification["record_id"] == ravi["id"]
        and notification["mutation_receipt"] == update["receipt"],
        "notification recipient or mutation dependency is incorrect",
    )
    require(
        notification["subject"].strip() and notification["message"].strip(),
        "talent operations notice must contain an outcome",
    )
    outcome = (notification["subject"] + " " + notification["message"]).casefold()
    require(
        "ravi patel" in outcome and "screening" in outcome and "offer-review" in outcome,
        "talent operations notice does not accurately identify the outcome",
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
        require(DATABASE.is_file(), "the executable recruiting environment was not used")
        noelle, ravi, snapshot = source_records()
        database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        database.row_factory = sqlite3.Row
        try:
            update, notice = verify_execution(database, noelle, ravi)
            verify_state(database, noelle, ravi, snapshot, update, notice)
        finally:
            database.close()
    except (VerificationError, OSError, sqlite3.Error, json.JSONDecodeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: parallel resolution, conditional scoped mutation, and dependent notice verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
