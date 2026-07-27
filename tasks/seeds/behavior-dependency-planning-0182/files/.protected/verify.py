#!/usr/bin/env python3
"""Protected deterministic verifier for the travel reconciliation Pi task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".travel-runtime" / "travel.sqlite3"
SEED = ROOT / ".protected" / "travel_seed.sql"
KEY_PATH = ROOT / ".protected" / "receipt.key"
RECEIPT = ROOT / "travel-reconciliation.receipt.json"
REPORT = ROOT / "travel-outcome.md"
TARGETS = (
    ("Reykjavík research trip", "Reykjavík"),
    ("Chicago volunteer summit", "Chicago"),
)
REASON = "Traveler chose a different itinerary"
RECIPIENT = "travel coordinator"
PROTECTED_HASHES = {
    ".gitignore": "0b6e2b99a6854173848d7e2130efd05627c11c06412c6b33a8818b1ef140a168",
    ".protected/receipt.key": "59e82c4958ee1bff6725ef91c12d07c52ab9d55e4b32a5a694eed6a0b1a7bca1",
    ".protected/setup.py": "8c6afaa6e6f9cbb3f1d7bfd8ecbb494c59d3b13be6ca39c802c7ad0e07b2ce25",
    ".protected/travel_seed.sql": "1fb25d452bff35682e6e0be26b62fc201f1b6716e878b98ec774266a60728a5d",
    "TRAVEL.md": "c11d1b1c6ba8a49c4b1b7da18b4e566f3a4d79a413608d925a9f067283af888b",
    "travelctl": "d11e4a6d4089753bddcc4a7b8e1594ae7796beff670280c1702e80cd5ec8f833",
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
        "date": row["trip_date"],
        "id": row["id"],
        "location": row["location"],
        "name": row["name"],
        "notes": row["notes"],
        "planner": row["planner"],
        "status": row["status"],
    }


def trip_state(database: sqlite3.Connection) -> list[list[Any]]:
    return [
        list(row)
        for row in database.execute(
            """
            SELECT id, name, location, trip_date, status, planner, notes,
                   cancellation_reason
              FROM trips
             ORDER BY id
            """
        )
    ]


def notification_state(database: sqlite3.Connection) -> list[list[Any]]:
    return [
        list(row)
        for row in database.execute(
            """
            SELECT sequence, trip_id, recipient, outcome, delivered
              FROM notifications
             ORDER BY sequence
            """
        )
    ]


def verify_protected_files() -> None:
    for relative, expected_hash in PROTECTED_HASHES.items():
        path = ROOT / relative
        require(path.is_file(), f"protected task file is missing: {relative}")
        require(
            file_sha256(path) == expected_hash,
            f"protected task file changed: {relative}",
        )


def verify_workspace_scope() -> None:
    allowed = {
        ".git",
        ".gitignore",
        ".protected",
        ".travel-runtime",
        "TRAVEL.md",
        "reference_driver.py",
        "travel-reconciliation.receipt.json",
        "travel-outcome.md",
        "travelctl",
    }
    unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    require(not unexpected, f"unexpected workspace artifacts: {', '.join(unexpected)}")


def canonical_state() -> tuple[
    list[dict[str, Any]],
    dict[str, list[tuple[Any, ...]]],
]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, location in TARGETS:
            rows = source.execute(
                """
                SELECT id, name, location, trip_date, status, planner, notes
                  FROM trips
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        require(records[1]["status"] == "draft", "protected Chicago fixture is invalid")
        snapshot = {
            "trips": [tuple(row) for row in trip_state(source)],
            "profiles": [
                tuple(row)
                for row in source.execute(
                    "SELECT owner, preference FROM saved_profiles ORDER BY owner"
                )
            ],
            "availability": [
                tuple(row)
                for row in source.execute(
                    """
                    SELECT location, trip_date, available_options
                      FROM availability
                     ORDER BY location, trip_date
                    """
                )
            ],
            "notifications": [],
        }
        return records, snapshot
    finally:
        source.close()


def shown(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def cancellation_result(record: dict[str, Any]) -> dict[str, Any]:
    outcome = f"{record['name']} cancelled"
    return {
        "cancellation_reason": REASON,
        "cancelled": True,
        "id": record["id"],
        "location": record["location"],
        "name": record["name"],
        "outcome": outcome,
        "status": "cancelled",
    }


def notification_result(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "delivered": True,
        "outcome": f"{record['name']} cancelled",
        "recipient": RECIPIENT,
    }


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "travel-outcome.md is missing")
    first, second = records
    cancelled = cancellation_result(second)
    notice = notification_result(second)
    expected = (
        "| Name | ID | Location | Date | Retrieved status |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| {shown(first.get('name'))} | {shown(first.get('id'))} | "
        f"{shown(first.get('location'))} | {shown(first.get('date'))} | "
        f"{shown(first.get('status'))} |\n"
        f"| {shown(second.get('name'))} | {shown(second.get('id'))} | "
        f"{shown(second.get('location'))} | {shown(second.get('date'))} | "
        f"{shown(second.get('status'))} |\n"
        f"Cancellation: {shown(cancelled.get('id'))} | "
        f"{shown(cancelled.get('status'))} | "
        f"{shown(cancelled.get('cancellation_reason'))} | "
        f"{shown(cancelled.get('outcome'))}\n"
        f"Notification: {shown(notice.get('recipient'))} | "
        f"{shown(notice.get('delivered'))} | {shown(notice.get('outcome'))}\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "travel-outcome.md is not the exact grounded scoped report",
    )


def require_interval(event: sqlite3.Row, label: str) -> tuple[int, int]:
    started = event["started_ns"]
    finished = event["finished_ns"]
    require(
        isinstance(started, int)
        and isinstance(finished, int)
        and started < finished,
        f"{label} has an invalid execution interval",
    )
    return int(started), int(finished)


def intervals_overlap(
    first: tuple[int, int],
    second: tuple[int, int],
) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def verify_searches(
    searches: list[sqlite3.Row],
    records: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    expected_by_scope = {
        (record["name"], record["location"]): record for record in records
    }
    observed: set[tuple[str, str]] = set()
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(searches, 1):
        arguments = json.loads(event["arguments_json"])
        require(set(arguments) == {"location", "name"}, "search used extra inputs")
        scope = (arguments["name"], arguments["location"])
        require(scope in expected_by_scope, "search targeted an unrequested trip")
        require(scope not in observed, "a required search was duplicated")
        observed.add(scope)
        record = expected_by_scope[scope]
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
            and event["result_digest"] == digest(expected_result)
            and event["outcome"] == "ok",
            "search did not return the unique requested stable ID",
        )
        intervals.append(require_interval(event, f"search event {index}"))
    require(observed == set(expected_by_scope), "both exact searches are required")
    require(intervals_overlap(intervals[0], intervals[1]), "searches did not overlap")
    require(
        len({event["pid"] for event in searches}) == 2
        and len({event["parent_pid"] for event in searches}) == 2,
        "searches were not separate sibling Bash calls",
    )
    return intervals


def verify_gets(
    gets: list[sqlite3.Row],
    records: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    expected_by_id = {record["id"]: record for record in records}
    observed: set[str] = set()
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(gets, 3):
        arguments = json.loads(event["arguments_json"])
        require(set(arguments) == {"id"}, "get used extra inputs")
        stable_id = arguments["id"]
        require(stable_id in expected_by_id, "get used an unresolved ID")
        require(stable_id not in observed, "a required get was duplicated")
        observed.add(stable_id)
        record = expected_by_id[stable_id]
        require(
            event["result_count"] == 1
            and event["sole_id"] == stable_id
            and event["status"] == record["status"]
            and event["outcome"] == "ok"
            and event["result_digest"] == digest({"record": record}),
            "get did not return the required complete record",
        )
        intervals.append(require_interval(event, f"get event {index}"))
    require(observed == set(expected_by_id), "both resolved trips must be retrieved")
    require(intervals_overlap(intervals[0], intervals[1]), "gets did not overlap")
    require(
        len({event["pid"] for event in gets}) == 2
        and len({event["parent_pid"] for event in gets}) == 2,
        "gets were not separate sibling Bash calls",
    )
    return intervals


def verify_cancel(event: sqlite3.Row, record: dict[str, Any]) -> tuple[int, int]:
    require(
        json.loads(event["arguments_json"]) == {"id": record["id"], "reason": REASON},
        "cancellation did not use the resolved Chicago ID and exact reason",
    )
    require(
        event["result_count"] == 1
        and event["sole_id"] == record["id"]
        and event["status"] == "cancelled"
        and event["outcome"] == "mutated"
        and event["result_digest"] == digest(cancellation_result(record)),
        "Chicago cancellation did not return the required successful mutation",
    )
    return require_interval(event, "cancel event")


def verify_notify(event: sqlite3.Row, record: dict[str, Any]) -> tuple[int, int]:
    outcome = f"{record['name']} cancelled"
    require(
        json.loads(event["arguments_json"])
        == {"outcome": outcome, "recipient": RECIPIENT},
        "notification did not use the requested recipient and returned outcome",
    )
    require(
        event["result_count"] == 1
        and event["sole_id"] == record["id"]
        and event["outcome"] == "delivered"
        and event["result_digest"] == digest(notification_result(record)),
        "notification did not return successful delivery",
    )
    return require_interval(event, "notify event")


def expected_receipt(
    records: list[dict[str, Any]],
    trips_sha256: str,
    notifications_sha256: str,
) -> dict[str, Any]:
    searches = [
        {
            "arguments": {"location": record["location"], "name": record["name"]},
            "operation": "search",
            "outcome": "ok",
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
            "status": None,
        }
        for record in records
    ]
    gets = [
        {
            "arguments": {"id": record["id"]},
            "operation": "get",
            "outcome": "ok",
            "result_count": 1,
            "result_digest": digest({"record": record}),
            "sole_id": record["id"],
            "status": record["status"],
        }
        for record in records
    ]
    searches.sort(key=canonical_json)
    gets.sort(key=canonical_json)
    chicago = records[1]
    cancel = {
        "arguments": {"id": chicago["id"], "reason": REASON},
        "operation": "cancel",
        "outcome": "mutated",
        "result_count": 1,
        "result_digest": digest(cancellation_result(chicago)),
        "sole_id": chicago["id"],
        "status": "cancelled",
    }
    notice_outcome = f"{chicago['name']} cancelled"
    notify = {
        "arguments": {"outcome": notice_outcome, "recipient": RECIPIENT},
        "operation": "notify",
        "outcome": "delivered",
        "result_count": 1,
        "result_digest": digest(notification_result(chicago)),
        "sole_id": chicago["id"],
        "status": None,
    }
    payload = {
        "notifications_sha256": notifications_sha256,
        "stages": [searches, gets, cancel, notify],
        "trips_sha256": trips_sha256,
        "version": 1,
    }
    receipt = dict(payload)
    key = bytes.fromhex(KEY_PATH.read_text(encoding="utf-8").strip())
    receipt["signature"] = hmac.new(
        key,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return receipt


def verify_receipt(
    records: list[dict[str, Any]],
    trips_sha256: str,
    notifications_sha256: str,
) -> None:
    require(RECEIPT.is_file(), "the executable did not emit its signed receipt")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    require(isinstance(receipt, dict), "receipt must be a JSON object")
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
        "receipt signature is invalid",
    )
    require(
        receipt == expected_receipt(records, trips_sha256, notifications_sha256),
        "receipt does not prove the exact scoped workflow",
    )


def verify_final_state(
    database: sqlite3.Connection,
    records: list[dict[str, Any]],
    snapshot: dict[str, list[tuple[Any, ...]]],
) -> tuple[str, str]:
    expected_trips = [list(row) for row in snapshot["trips"]]
    chicago_id = records[1]["id"]
    for row in expected_trips:
        if row[0] == chicago_id:
            row[4] = "cancelled"
            row[7] = REASON
    require(trip_state(database) == expected_trips, "travel records changed outside scope")
    profiles = [
        tuple(row)
        for row in database.execute(
            "SELECT owner, preference FROM saved_profiles ORDER BY owner"
        )
    ]
    availability = [
        tuple(row)
        for row in database.execute(
            """
            SELECT location, trip_date, available_options
              FROM availability
             ORDER BY location, trip_date
            """
        )
    ]
    require(profiles == snapshot["profiles"], "saved profiles changed")
    require(availability == snapshot["availability"], "availability changed")
    expected_notifications = [
        [
            1,
            chicago_id,
            RECIPIENT,
            f"{records[1]['name']} cancelled",
            1,
        ]
    ]
    require(
        notification_state(database) == expected_notifications,
        "notification state is not exactly one post-cancellation delivery",
    )
    return digest(trip_state(database)), digest(notification_state(database))


def verify_execution(
    records: list[dict[str, Any]],
    snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the travel executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 6, "expected exactly six travel operations")
        require(
            [event["sequence"] for event in events] == [1, 2, 3, 4, 5, 6],
            "operation sequence is incomplete or reordered",
        )
        require(
            [event["operation"] for event in events]
            == ["search", "search", "get", "get", "cancel", "notify"],
            "operation dependency layers are incorrect",
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

        search_intervals = verify_searches(events[:2], records)
        get_intervals = verify_gets(events[2:4], records)
        require(
            max(interval[1] for interval in search_intervals)
            < min(interval[0] for interval in get_intervals),
            "a get began before both searches returned",
        )
        require(
            {event["parent_pid"] for event in events[:2]}.isdisjoint(
                {event["parent_pid"] for event in events[2:4]}
            ),
            "search and get phases were not separate assistant actions",
        )
        cancel_interval = verify_cancel(events[4], records[1])
        require(
            max(interval[1] for interval in get_intervals) < cancel_interval[0],
            "cancellation began before both gets returned",
        )
        notify_interval = verify_notify(events[5], records[1])
        require(
            cancel_interval[1] < notify_interval[0],
            "notification began before cancellation succeeded",
        )

        trips_sha256, notifications_sha256 = verify_final_state(
            database, records, snapshot
        )
    finally:
        database.close()

    verify_receipt(records, trips_sha256, notifications_sha256)


def main() -> int:
    try:
        verify_protected_files()
        verify_workspace_scope()
        records, snapshot = canonical_state()
        verify_report(records)
        verify_execution(records, snapshot)
    except (VerificationError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print(
        "verification passed: parallel resolution preceded one scoped cancellation and its dependent notification"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
