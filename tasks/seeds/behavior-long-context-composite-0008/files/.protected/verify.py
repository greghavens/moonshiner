#!/usr/bin/env python3
"""Protected deterministic verification for the shipment lookup task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".shipment-runtime"
DATABASE = RUNTIME / "shipments.sqlite3"
AUDIT = RUNTIME / "execution.sqlite3"
SEED = ROOT / ".protected" / "shipments_seed.sql"
REPORT = ROOT / "answer.txt"
RECEIPT = ROOT / "shipment-audit.receipt.json"
RECEIPT_KEY = bytes.fromhex(
    "b351c3126539a2c8a72562fd281f0c6b8801319123c6c9de3a483e9a6967b1d2"
)
TARGET = {
    "location": "Portland",
    "name": "Parcel Driftwood",
    "status": "active",
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


def full_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "carrier": row["carrier"],
        "date": row["ship_date"],
        "id": row["id"],
        "last_scan": row["last_scan"],
        "location": row["location"],
        "name": row["name"],
        "notes": row["notes"],
        "service_level": row["service_level"],
        "status": row["status"],
    }


def data_snapshot(database: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "shipments": [
            tuple(row)
            for row in database.execute(
                """
                SELECT id, name, location, ship_date, status, carrier,
                       service_level, last_scan, notes
                  FROM shipments
                 ORDER BY id
                """
            )
        ],
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
                SELECT location, ship_date, dock_capacity
                  FROM availability
                 ORDER BY location, ship_date
                """
            )
        ],
        "notifications": [
            tuple(row)
            for row in database.execute(
                """
                SELECT sequence, shipment_id, recipient, note
                  FROM notifications
                 ORDER BY sequence
                """
            )
        ],
    }


def canonical_state() -> tuple[dict[str, Any], dict[str, list[tuple[Any, ...]]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        rows = source.execute(
            """
            SELECT id, name, location, ship_date, status, carrier,
                   service_level, last_scan, notes
              FROM shipments
             WHERE name = ? AND location = ? AND status = ?
             ORDER BY id
            """,
            (TARGET["name"], TARGET["location"], TARGET["status"]),
        ).fetchall()
        require(len(rows) == 1, "the protected target must resolve uniquely")
        return full_record(rows[0]), data_snapshot(source)
    finally:
        source.close()


def verify_report(record: dict[str, Any]) -> None:
    require(REPORT.is_file(), "answer.txt is missing")
    expected = (
        f"Found: {record['name']} in {record['location']} is {record['status']} "
        f"with stable ID `{record['id']}`.\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "answer.txt is not the exact one-sentence result from the full record",
    )


def expected_operations(record: dict[str, Any]) -> list[dict[str, Any]]:
    search_result = {
        "matches": [
            {
                "id": record["id"],
                "location": record["location"],
                "name": record["name"],
            }
        ]
    }
    return [
        {
            "arguments": dict(TARGET),
            "operation": "search",
            "result_count": 1,
            "result_digest": digest(search_result),
            "sole_id": record["id"],
        },
        {
            "arguments": {"id": record["id"]},
            "operation": "get",
            "result_count": 1,
            "result_digest": digest({"record": record}),
            "sole_id": record["id"],
        },
    ]


def verify_receipt(record: dict[str, Any], state_sha256: str) -> None:
    require(RECEIPT.is_file(), "the genuine executable did not emit its receipt")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    require(isinstance(receipt, dict), "the executable receipt is malformed")
    signature = receipt.get("signature")
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    authentic = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, authentic),
        "the executable receipt is not authentic",
    )
    expected = {
        "operations": expected_operations(record),
        "signature": authentic,
        "state_sha256": state_sha256,
        "version": 1,
    }
    require(receipt == expected, "the receipt does not prove the required workflow")


def verify_execution(
    record: dict[str, Any],
    canonical_snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the shipment data environment was not initialized")
    require(AUDIT.is_file(), "the executable execution journal is missing")

    state_sha256 = hashlib.sha256(DATABASE.read_bytes()).hexdigest()
    data = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    data.row_factory = sqlite3.Row
    audit = sqlite3.connect(f"file:{AUDIT}?mode=ro", uri=True)
    audit.row_factory = sqlite3.Row
    try:
        require(
            data_snapshot(data) == canonical_snapshot,
            "shipment state changed during the read-only lookup",
        )
        baseline = audit.execute(
            "SELECT value FROM metadata WHERE key = 'baseline_sha256'"
        ).fetchone()
        require(
            baseline is not None and baseline["value"] == state_sha256,
            "the shipment database changed after sandbox initialization",
        )

        events = audit.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 2, "expected exactly one search and one retrieval")
        require(
            [event["operation"] for event in events] == ["search", "get"],
            "the workflow must be a scoped search followed by one get",
        )
        require(
            all(
                event["finished_ns"] is not None
                and event["error"] is None
                and event["violation"] == 0
                and event["result_digest"] is not None
                for event in events
            ),
            "a forbidden, failed, incomplete, or ungrounded operation was recorded",
        )

        search, get = events
        search_arguments = json.loads(search["arguments_json"])
        require(
            search_arguments == TARGET,
            "the search was not scoped to the exact requested name, location, and status",
        )
        search_result = {
            "matches": [
                {
                    "id": record["id"],
                    "location": record["location"],
                    "name": record["name"],
                }
            ]
        }
        require(
            search["result_count"] == 1
            and search["sole_id"] == record["id"]
            and search["result_digest"] == digest(search_result),
            "the scoped search did not uniquely resolve the protected target",
        )

        require(
            json.loads(get["arguments_json"]) == {"id": record["id"]},
            "the get did not use the stable ID returned by the search",
        )
        require(
            get["result_count"] == 1
            and get["sole_id"] == record["id"]
            and get["result_digest"] == digest({"record": record}),
            "the get did not retrieve the required full record",
        )
        require(
            search["action_id"] != get["action_id"],
            "search and get were not separate direct invocations in separate shell actions",
        )
        require(
            int(search["finished_ns"]) < int(get["started_ns"]),
            "the get began before the search response returned",
        )
    finally:
        audit.close()
        data.close()

    verify_receipt(record, state_sha256)


def main() -> int:
    try:
        record, snapshot = canonical_state()
        verify_report(record)
        verify_execution(record, snapshot)
    except (OSError, sqlite3.Error, json.JSONDecodeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact answer, scoped search, dependent full-record get, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
