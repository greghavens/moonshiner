#!/usr/bin/env python3
"""Protected deterministic verifier for the recruiting correction task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".recruiting-runtime" / "candidates.sqlite3"
SEED = ROOT / ".protected" / "candidates_seed.sql"
REPORT = ROOT / "candidate-report.txt"
RECEIPT = ROOT / "candidate-report.receipt.json"
RECEIPT_KEY = bytes.fromhex(
    "f35f525430e64270fe1d44e985053524602be059b7539c8bad97ea8dafab7547"
)
LOOKUPS = (
    ("Morgan Shah — facilities planner", "Workplace Strategy"),
    ("Morgan Shah — facilities planner", "Operations"),
)


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def candidate_state(database: sqlite3.Connection) -> list[list[Any]]:
    return [
        list(row)
        for row in database.execute(
            """
            SELECT id, name, department, interview_date, status, recruiter,
                   application_source, notes
              FROM candidates
             ORDER BY id
            """
        )
    ]


def notification_state(database: sqlite3.Connection) -> list[list[Any]]:
    return [
        list(row)
        for row in database.execute(
            """
            SELECT sequence, candidate_id, message
              FROM notifications
             ORDER BY sequence
            """
        )
    ]


def full_record(row: sqlite3.Row) -> dict[str, Any]:
    record = {
        "application_source": row["application_source"],
        "department": row["department"],
        "id": row["id"],
        "name": row["name"],
        "notes": row["notes"],
        "recruiter": row["recruiter"],
        "status": row["status"],
    }
    if row["interview_date"] is not None:
        record["interview_date"] = row["interview_date"]
    return record


def canonical_environment() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, department in LOOKUPS:
            rows = source.execute(
                """
                SELECT id, name, department, interview_date, status, recruiter,
                       application_source, notes
                  FROM candidates
                 WHERE name = ? AND department = ?
                 ORDER BY id
                """,
                (name, department),
            ).fetchall()
            require(len(rows) == 1, "each protected lookup must resolve uniquely")
            records.append(full_record(rows[0]))
        state = {
            "candidates": candidate_state(source),
            "notifications": notification_state(source),
        }
        return records, state
    finally:
        source.close()


def shown(value: Any) -> str:
    return value if isinstance(value, str) and value else "unknown"


def verify_report(corrected: dict[str, Any]) -> None:
    require(REPORT.is_file(), "candidate-report.txt is missing")
    require(
        REPORT.read_bytes().endswith(b"\n"),
        "candidate-report.txt must end with a newline",
    )
    expected = [
        f"ID: {shown(corrected.get('id'))}",
        f"Interview date: {shown(corrected.get('interview_date'))}",
        f"Status: {shown(corrected.get('status'))}",
    ]
    lines = REPORT.read_text(encoding="utf-8").splitlines()
    require(
        lines == expected and all(line.strip() for line in lines),
        "candidate-report.txt is not the exact grounded corrected three-line report",
    )


def expected_event_pairs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for (name, department), record in zip(LOOKUPS, records, strict=True):
        search_result = {
            "matches": [
                {
                    "department": record["department"],
                    "id": record["id"],
                    "name": record["name"],
                }
            ]
        }
        events.append(
            {
                "arguments": {"department": department, "name": name},
                "error": None,
                "operation": "search",
                "result_count": 1,
                "result_digest": digest(search_result),
                "sole_id": record["id"],
                "violation": False,
            }
        )
        events.append(
            {
                "arguments": {"id": record["id"]},
                "error": None,
                "operation": "open",
                "result_count": 1,
                "result_digest": digest({"record": record}),
                "sole_id": record["id"],
                "violation": False,
            }
        )
    return events


def verify_execution(
    records: list[dict[str, Any]],
    canonical_state: dict[str, Any],
) -> None:
    require(DATABASE.is_file(), "the recruiting executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        observed_state = {
            "candidates": candidate_state(database),
            "notifications": notification_state(database),
        }
        require(
            observed_state == canonical_state,
            "candidate or notification state changed during the read-only task",
        )

        rows = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(rows) == 4, "expected exactly two searches and two opens")
        require(
            [row["operation"] for row in rows]
            == ["search", "open", "search", "open"],
            "the two turns must each perform one search followed by its open",
        )
        require(
            all(
                row["violation"] == 0
                and row["error"] is None
                and row["finished_ns"] is not None
                for row in rows
            ),
            "a forbidden, failed, or incomplete recruiting operation was recorded",
        )

        expected_events = expected_event_pairs(records)
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
        require(
            actual_events == expected_events,
            "the journal does not prove the original lookup and corrected exact lookup",
        )
        state_sha256 = digest(observed_state)
    finally:
        database.close()

    require(RECEIPT.is_file(), "the executable did not emit its signed receipt")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    require(isinstance(receipt, dict), "the executable receipt is invalid")
    signature = receipt.get("signature")
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    authentic = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, authentic),
        "the recruiting receipt was not authentically generated by the executable",
    )
    require(
        payload
        == {
            "events": expected_event_pairs(records),
            "state_sha256": state_sha256,
            "version": 1,
        },
        "the signed receipt does not prove the exact corrected read-only workflow",
    )


def main() -> int:
    try:
        records, state = canonical_environment()
        verify_report(records[1])
        verify_execution(records, state)
    except (OSError, sqlite3.Error, json.JSONDecodeError, VerificationError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print("verification passed: corrected candidate lookup is grounded and read-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
