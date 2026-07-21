#!/usr/bin/env python3
"""Protected deterministic verifier for the customer-case Pi task."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".case-runtime" / "cases.sqlite3"
SEED = ROOT / ".protected" / "cases_seed.sql"
REPORT = ROOT / "case-handoff.md"
RECEIPT = ROOT / "case-handoff.receipt.json"
RECEIPT_KEY = bytes.fromhex(
    "8e4c942b622a8fd7613c078d2d2c25e3b19435115919f9a4b91563a0b147a8cf"
)
TARGETS = (
    ("Kiosk Login Loop", "Civic Museum"),
    ("Receipt Export Missing Rows", "Delta Housing"),
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
    record = {
        "customer_note": row["customer_note"],
        "id": row["id"],
        "location": row["location"],
        "name": row["name"],
        "owner": row["owner"],
        "status": row["status"],
    }
    if row["case_date"] is not None:
        record["date"] = row["case_date"]
    return record


def case_state(database: sqlite3.Connection) -> list[list[Any]]:
    return [
        list(row)
        for row in database.execute(
            """
            SELECT id, name, location, case_date, status, owner, customer_note
              FROM cases
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
        for name, location in TARGETS:
            rows = source.execute(
                """
                SELECT id, name, location, case_date, status, owner, customer_note
                  FROM cases
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        snapshot = {
            "cases": [tuple(row) for row in case_state(source)],
            "preferences": [
                tuple(row)
                for row in source.execute(
                    "SELECT owner, preference FROM saved_preferences ORDER BY owner"
                )
            ],
            "availability": [
                tuple(row)
                for row in source.execute(
                    """
                    SELECT location, support_date, staff_available
                      FROM availability
                     ORDER BY location, support_date
                    """
                )
            ],
            "notifications": [],
        }
        return records, snapshot
    finally:
        source.close()


def shown(value: Any) -> str:
    return value if isinstance(value, str) and value else "unknown"


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "case-handoff.md is missing")
    first, second = records
    first_status = shown(first.get("status"))
    second_status = shown(second.get("status"))
    if first_status == "unknown" or second_status == "unknown":
        comparison = "Status comparison: unavailable."
    elif first_status == second_status:
        comparison = f"Status comparison: same ({first_status})."
    else:
        comparison = (
            f"Status comparison: different ({first_status} vs {second_status})."
        )

    expected_rows: list[list[str]] = []
    for record in records:
        expected_rows.append(
            [
                shown(record.get("name")),
                shown(record.get("id")),
                shown(record.get("date")),
                shown(record.get("status")),
            ]
        )

    lines = REPORT.read_text(encoding="utf-8").splitlines()
    require(
        len(lines) == 5 and all(line.strip() for line in lines),
        "case-handoff.md must contain exactly five nonblank lines",
    )

    def cells(line: str) -> list[str]:
        require(
            line.startswith("|") and line.endswith("|"),
            "case-handoff.md must use a pipe-delimited Markdown table",
        )
        return [cell.strip() for cell in line[1:-1].split("|")]

    require(
        cells(lines[0]) == ["Name", "ID", "Date", "Status"],
        "case-handoff.md does not use the requested table columns",
    )
    separators = cells(lines[1])
    require(
        len(separators) == 4
        and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separators),
        "case-handoff.md does not contain a four-column Markdown separator",
    )
    require(
        cells(lines[2]) == expected_rows[0]
        and cells(lines[3]) == expected_rows[1]
        and lines[4] == comparison,
        "case-handoff.md does not contain the exact grounded two-row audit",
    )


def expected_receipt(records: list[dict[str, Any]], state_sha256: str) -> dict[str, Any]:
    searches = [
        {
            "arguments": {"location": record["location"], "query": record["name"]},
            "operation": "search",
            "result_count": 1,
            "result_digest": digest(
                {
                    "matches": [
                        {
                            "id": record["id"],
                            "location": record["location"],
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


def verify_execution(
    records: list[dict[str, Any]],
    snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the customer-case executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 4, "expected exactly four case-data operations")
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
        expected_by_key = {
            (record["name"], record["location"]): record for record in records
        }
        observed_keys: set[tuple[str, str]] = set()
        for event in searches:
            arguments = json.loads(event["arguments_json"])
            require(set(arguments) == {"location", "query"}, "search used extra inputs")
            key = (arguments["query"], arguments["location"])
            require(key in expected_by_key, "search targeted an unrequested case")
            require(key not in observed_keys, "a requested branch was searched twice")
            observed_keys.add(key)
            record = expected_by_key[key]
            expected_result = {
                "matches": [
                    {
                        "id": record["id"],
                        "location": record["location"],
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
                "get did not return the required full case record",
            )
        require(observed_ids == set(expected_by_id), "both resolved branches are required")

        require(
            len({event["pid"] for event in searches}) == 2
            and len({event["parent_pid"] for event in searches}) == 1,
            "searches were not separate processes from one shell-tool action",
        )
        require(
            max(event["started_ns"] for event in searches)
            < min(event["finished_ns"] for event in searches),
            "the search processes did not overlap",
        )
        require(
            len({event["pid"] for event in gets}) == 2
            and len({event["parent_pid"] for event in gets}) == 1,
            "gets were not separate processes from one shell-tool action",
        )
        require(
            max(event["started_ns"] for event in gets)
            < min(event["finished_ns"] for event in gets),
            "the get processes did not overlap",
        )
        require(
            max(event["finished_ns"] for event in searches)
            < min(event["started_ns"] for event in gets),
            "gets began before both search responses returned",
        )

        current = {
            "cases": [tuple(row) for row in case_state(database)],
            "preferences": [
                tuple(row)
                for row in database.execute(
                    "SELECT owner, preference FROM saved_preferences ORDER BY owner"
                )
            ],
            "availability": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT location, support_date, staff_available
                      FROM availability
                     ORDER BY location, support_date
                    """
                )
            ],
            "notifications": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT sequence, case_id, recipient, note
                      FROM notifications
                     ORDER BY sequence
                    """
                )
            ],
        }
        require(current == snapshot, "customer-case state changed during the audit")
        state_sha256 = digest(case_state(database))
    finally:
        database.close()

    verify_receipt(records, state_sha256)


def main() -> int:
    try:
        records, snapshot = canonical_state()
        verify_report(records)
        verify_execution(records, snapshot)
    except (
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        VerificationError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: grounded report and dependency-gated concurrent execution verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
