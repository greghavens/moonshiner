#!/usr/bin/env python3
"""Protected deterministic verifier for the candidate-audit Pi task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".candidate-runtime" / "candidates.sqlite3"
SEED = ROOT / ".protected" / "candidates_seed.sql"
REPORT = ROOT / "candidate-audit.txt"
RECEIPT = ROOT / "candidate-audit.receipt.json"
RECEIPT_KEY = bytes.fromhex(
    "772b40474c5e4892d8e3a87ad0533811f2fe66afc96b6e46c120fb65ae3466e7"
)
TARGETS = (
    ("Morgan Iqbal — Fleet Analyst", "Operations"),
    ("Riley Chen — Grants Manager", "Development"),
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


def full_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "date": row["record_date"],
        "department": row["department"],
        "id": row["id"],
        "name": row["name"],
        "notes": row["notes"],
        "recruiter": row["recruiter"],
        "requisition": row["requisition"],
        "status": row["status"],
    }


def candidate_state(database: sqlite3.Connection) -> list[list[Any]]:
    return [
        list(row)
        for row in database.execute(
            """
            SELECT id, name, department, record_date, status, recruiter,
                   requisition, notes
              FROM candidates
             ORDER BY id
            """
        )
    ]


def canonical_state() -> tuple[list[dict[str, Any]], dict[str, list[tuple[Any, ...]]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, department in TARGETS:
            rows = source.execute(
                """
                SELECT id, name, department, record_date, status, recruiter,
                       requisition, notes
                  FROM candidates
                 WHERE name = ? AND department = ?
                 ORDER BY id
                """,
                (name, department),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        snapshot = {
            "candidates": [tuple(row) for row in candidate_state(source)],
            "notifications": [],
        }
        return records, snapshot
    finally:
        source.close()


def comparison_lines(first: dict[str, Any], second: dict[str, Any]) -> tuple[str, str]:
    status_relation = "same" if first["status"] == second["status"] else "different"
    if first["date"] == second["date"]:
        date_relation = "same"
    elif first["date"] < second["date"]:
        date_relation = "first earlier"
    else:
        date_relation = "second earlier"
    return (
        f"Status comparison: {status_relation} "
        f"({first['status']} vs {second['status']}).",
        f"Date comparison: {date_relation} ({first['date']} vs {second['date']}).",
    )


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "candidate-audit.txt is missing")
    text = REPORT.read_text(encoding="utf-8")
    first, second = records
    status_line, date_line = comparison_lines(first, second)
    expected = (
        f"{first['name']} | {first['id']} | {first['status']} | {first['date']}\n"
        f"{second['name']} | {second['id']} | {second['status']} | {second['date']}\n"
        f"{status_line}\n"
        f"{date_line}\n"
        "No changes made.\n"
    )
    require(
        text == expected,
        "candidate-audit.txt does not contain the exact grounded five-line audit",
    )


def expected_receipt(records: list[dict[str, Any]], state_sha256: str) -> dict[str, Any]:
    searches = [
        {
            "arguments": {
                "department": record["department"],
                "name": record["name"],
            },
            "operation": "search",
            "result_count": 1,
            "result_digest": digest(
                {
                    "matches": [
                        {
                            "department": record["department"],
                            "id": record["id"],
                            "name": record["name"],
                        }
                    ]
                }
            ),
            "sole_id": record["id"],
        }
        for record in records
    ]
    gets = [
        {
            "arguments": {"id": record["id"]},
            "operation": "get",
            "result_count": 1,
            "result_digest": digest({"record": record}),
            "sole_id": record["id"],
        }
        for record in records
    ]
    searches.sort(key=canonical_json)
    gets.sort(key=canonical_json)
    payload = {
        "stages": [searches, gets],
        "state_sha256": state_sha256,
        "version": 1,
    }
    receipt = dict(payload)
    receipt["signature"] = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return receipt


def verify_receipt(records: list[dict[str, Any]], state_sha256: str) -> None:
    require(RECEIPT.is_file(), "the executable did not emit its audit receipt")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    require(isinstance(receipt, dict), "the executable receipt must be a JSON object")
    signature = receipt.get("signature")
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    authentic = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, authentic),
        "the audit receipt was not authentically generated by the executable",
    )
    require(
        receipt == expected_receipt(records, state_sha256),
        "the receipt does not prove the exact scoped workflow",
    )


def verify_searches(
    searches: list[sqlite3.Row],
    records: list[dict[str, Any]],
) -> None:
    expected_by_key = {
        (record["name"], record["department"]): record for record in records
    }
    observed_keys: set[tuple[str, str]] = set()
    for event in searches:
        arguments = json.loads(event["arguments_json"])
        require(set(arguments) == {"department", "name"}, "search used extra inputs")
        key = (arguments["name"], arguments["department"])
        require(key in expected_by_key, "search targeted an unrequested candidate")
        require(key not in observed_keys, "a requested branch was searched twice")
        observed_keys.add(key)
        record = expected_by_key[key]
        expected_result = {
            "matches": [
                {
                    "department": record["department"],
                    "id": record["id"],
                    "name": record["name"],
                }
            ]
        }
        require(
            event["result_count"] == 1
            and event["sole_id"] == record["id"]
            and event["result_digest"] == digest(expected_result),
            "search did not return the unique requested stable ID",
        )
    require(observed_keys == set(expected_by_key), "both exact searches are required")


def verify_gets(gets: list[sqlite3.Row], records: list[dict[str, Any]]) -> None:
    expected_by_id = {record["id"]: record for record in records}
    observed_ids: set[str] = set()
    for event in gets:
        arguments = json.loads(event["arguments_json"])
        require(set(arguments) == {"id"}, "get used extra inputs")
        stable_id = arguments["id"]
        require(stable_id in expected_by_id, "get did not use a resolved target ID")
        require(stable_id not in observed_ids, "a resolved branch was retrieved twice")
        observed_ids.add(stable_id)
        record = expected_by_id[stable_id]
        require(
            event["result_count"] == 1
            and event["sole_id"] == stable_id
            and event["result_digest"] == digest({"record": record}),
            "get did not return the required complete candidate record",
        )
    require(observed_ids == set(expected_by_id), "both resolved branches are required")


def verify_concurrency(searches: list[sqlite3.Row], gets: list[sqlite3.Row]) -> None:
    require(
        len({event["pid"] for event in searches}) == 2
        and len({event["parent_pid"] for event in searches}) == 1
        and len({event["action_id"] for event in searches}) == 1,
        "searches were not separate processes from one shell-tool action",
    )
    require(
        max(event["started_ns"] for event in searches)
        < min(event["finished_ns"] for event in searches),
        "the search processes did not overlap",
    )
    require(
        len({event["pid"] for event in gets}) == 2
        and len({event["parent_pid"] for event in gets}) == 1
        and len({event["action_id"] for event in gets}) == 1,
        "gets were not separate processes from one shell-tool action",
    )
    require(
        max(event["started_ns"] for event in gets)
        < min(event["finished_ns"] for event in gets),
        "the get processes did not overlap",
    )
    require(
        {event["action_id"] for event in searches}.isdisjoint(
            {event["action_id"] for event in gets}
        ),
        "searches and gets must occur in separate shell-tool actions",
    )
    require(
        max(event["finished_ns"] for event in searches)
        < min(event["started_ns"] for event in gets),
        "gets began before both search responses returned",
    )


def verify_execution(
    records: list[dict[str, Any]],
    snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the candidate executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 4, "expected exactly four candidate-data operations")
        require(
            [event["operation"] for event in events] == ["search", "search", "get", "get"],
            "operations must be two searches immediately followed by two gets",
        )
        require(
            all(
                event["violation"] == 0
                and event["error"] is None
                and event["finished_ns"] is not None
                and event["result_digest"] is not None
                for event in events
            ),
            "a forbidden, failed, incomplete, or ungrounded operation was recorded",
        )

        searches = events[:2]
        gets = events[2:]
        verify_searches(searches, records)
        verify_gets(gets, records)
        verify_concurrency(searches, gets)

        current = {
            "candidates": [tuple(row) for row in candidate_state(database)],
            "notifications": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT sequence, candidate_id, recipient, note
                      FROM notifications
                     ORDER BY sequence
                    """
                )
            ],
        }
        require(current == snapshot, "candidate state changed during the read-only audit")
        state_sha256 = digest(candidate_state(database))
    finally:
        database.close()

    verify_receipt(records, state_sha256)


def main() -> int:
    try:
        records, snapshot = canonical_state()
        verify_report(records)
        verify_execution(records, snapshot)
    except (OSError, sqlite3.Error, json.JSONDecodeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: grounded audit and dependency-gated concurrent execution verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
