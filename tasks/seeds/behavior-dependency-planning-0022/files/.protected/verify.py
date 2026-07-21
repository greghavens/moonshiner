#!/usr/bin/env python3
"""Protected deterministic verification for the travel-audit Pi task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "target" / "travel.sqlite3"
RECEIPT = ROOT / "travel-audit.receipt.json"
FIRST_KEY = ("Oslo Design Workshop", "Oslo")
SECOND_KEY = ("Lisbon Partner Summit", "Lisbon")
RECEIPT_KEY = bytes.fromhex(
    "49d8e75be787095606127bafa824101806e52cf889f90ad8f7653c774a4617e8"
)


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def state_digest(snapshot: list[tuple[object, ...]]) -> str:
    return hashlib.sha256(
        canonical_json([list(row) for row in snapshot]).encode("utf-8")
    ).hexdigest()


def canonical_state() -> tuple[list[sqlite3.Row], list[tuple[object, ...]], str]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(
            (ROOT / ".protected" / "travel_seed.sql").read_text(encoding="utf-8")
        )
        records: list[sqlite3.Row] = []
        for name, location in (FIRST_KEY, SECOND_KEY):
            rows = source.execute(
                """
                SELECT id, name, location, trip_date, status, owner, notes
                  FROM trips
                 WHERE name = ? AND location = ?
                """,
                (name, location),
            ).fetchall()
            require(
                len(rows) == 1,
                "protected source data must resolve each requested branch uniquely",
            )
            records.append(rows[0])
        snapshot = [
            tuple(row)
            for row in source.execute(
                """
                SELECT id, name, location, trip_date, status, owner, notes
                  FROM trips
                 ORDER BY id
                """
            )
        ]
        return records, snapshot, state_digest(snapshot)
    finally:
        source.close()


def expected_receipt(records: list[sqlite3.Row], digest: str) -> dict[str, object]:
    searches = [
        {
            "arguments": {"location": record["location"], "name": record["name"]},
            "operation": "search",
            "result_count": 1,
            "sole_id": record["id"],
        }
        for record in records
    ]
    gets = [
        {
            "arguments": {"id": record["id"]},
            "operation": "get",
            "result_count": 1,
            "sole_id": record["id"],
        }
        for record in records
    ]
    searches.sort(key=canonical_json)
    gets.sort(key=canonical_json)
    payload: dict[str, object] = {
        "stages": [searches, gets],
        "state_sha256": digest,
        "version": 1,
    }
    receipt = dict(payload)
    receipt["signature"] = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return receipt


def verify_report(records: list[sqlite3.Row]) -> None:
    report = ROOT / "itinerary-audit.txt"
    require(report.is_file(), "itinerary-audit.txt is missing")
    ordered = sorted(records, key=lambda record: (record["trip_date"], record["id"]))
    first, second = ordered
    if first["status"] == second["status"]:
        comparison = f"The retrieved statuses match: {first['status']}."
    else:
        comparison = (
            "The retrieved statuses differ: "
            f"{first['status']} versus {second['status']}."
        )
    expected = (
        f"{first['trip_date']} | {first['name']} | {first['location']} | "
        f"{first['id']} | {first['status']}\n"
        f"{second['trip_date']} | {second['name']} | {second['location']} | "
        f"{second['id']} | {second['status']}\n"
        f"{comparison}\n"
    )
    require(
        report.read_text(encoding="utf-8") == expected,
        "itinerary-audit.txt does not match the retrieved date order and status comparison",
    )


def verify_receipt(records: list[sqlite3.Row], digest: str) -> None:
    require(RECEIPT.is_file(), "the trip executable did not emit its audit receipt")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    require(isinstance(receipt, dict), "trip audit receipt must be a JSON object")
    signature = receipt.get("signature")
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    calculated = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, calculated),
        "trip audit receipt is not authentically executable-generated",
    )
    require(
        receipt == expected_receipt(records, digest),
        "trip audit receipt does not prove the required scoped workflow",
    )


def verify_runtime(
    records: list[sqlite3.Row],
    snapshot: list[tuple[object, ...]],
) -> None:
    require(DATABASE.is_file(), "the executable travel environment was not initialized")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        current = [
            tuple(row)
            for row in database.execute(
                """
                SELECT id, name, location, trip_date, status, owner, notes
                  FROM trips
                 ORDER BY id
                """
            )
        ]
        require(current == snapshot, "trip state changed during the read-only audit")
        notifications = database.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        require(notifications == 0, "notification state changed during the audit")

        rows = database.execute("SELECT * FROM audit_log ORDER BY sequence").fetchall()
        require(len(rows) == 4, "expected exactly four audited trip operations")
        require(
            [row["operation"] for row in rows] == ["search", "search", "get", "get"],
            "operations must be two searches followed immediately by two gets",
        )
        require(
            all(row["violation"] == 0 and row["error"] is None for row in rows),
            "a forbidden or failed trip operation was recorded",
        )
        require(
            all(row["finished_ns"] is not None for row in rows),
            "every trip operation must finish",
        )

        searches, gets = rows[:2], rows[2:]
        expected_by_key = {
            (record["name"], record["location"]): record for record in records
        }
        observed_keys: set[tuple[str, str]] = set()
        for row in searches:
            arguments = json.loads(row["arguments_json"])
            require(
                set(arguments) == {"name", "location"},
                "search used unexpected inputs",
            )
            key = (arguments["name"], arguments["location"])
            require(key in expected_by_key, "search targeted an out-of-scope trip")
            require(key not in observed_keys, "a requested branch was searched more than once")
            observed_keys.add(key)
            record = expected_by_key[key]
            require(
                row["result_count"] == 1 and row["sole_id"] == record["id"],
                "search did not resolve to its one stable ID",
            )
        require(observed_keys == set(expected_by_key), "both requested branches must be searched")
        require(
            len({row["parent_pid"] for row in searches}) == 1,
            "searches were not two real processes in one shell-tool action",
        )
        require(
            max(row["started_ns"] for row in searches)
            <= min(row["finished_ns"] for row in searches),
            "search executions did not overlap",
        )

        expected_ids = {str(record["id"]) for record in records}
        observed_ids: set[str] = set()
        for row in gets:
            arguments = json.loads(row["arguments_json"])
            require(set(arguments) == {"id"}, "get used unexpected inputs")
            trip_id = arguments["id"]
            require(
                trip_id in expected_ids,
                "get did not use a stable ID returned by a requested search",
            )
            require(trip_id not in observed_ids, "a resolved branch was retrieved more than once")
            observed_ids.add(trip_id)
            require(
                row["result_count"] == 1 and row["sole_id"] == trip_id,
                "get did not return one full trip record",
            )
        require(observed_ids == expected_ids, "both resolved branches must be retrieved")
        require(
            len({row["parent_pid"] for row in gets}) == 1,
            "gets were not two real processes in one shell-tool action",
        )
        require(
            max(row["started_ns"] for row in gets)
            <= min(row["finished_ns"] for row in gets),
            "get executions did not overlap",
        )
        require(
            max(row["finished_ns"] for row in searches)
            < min(row["started_ns"] for row in gets),
            "gets must wait for both search results",
        )
    finally:
        database.close()


def main() -> int:
    try:
        records, snapshot, digest = canonical_state()
        verify_report(records)
        verify_receipt(records, digest)
        verify_runtime(records, snapshot)
    except (
        VerificationError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: ordered audit, parallel dependency flow, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
