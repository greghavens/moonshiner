#!/usr/bin/env python3
"""Protected deterministic verifier for the appointment-audit Pi task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".appointment-runtime" / "appointments.sqlite3"
SEED = ROOT / ".protected" / "appointments_seed.sql"
REPORT = ROOT / "appointment-handoff.md"
RECEIPT = ROOT / "appointment-handoff.receipt.json"
RECEIPT_KEY = bytes.fromhex(
    "dbaea3a58f49641349123da00d1a13a237132b927f1bd7d0ac480b6fc9ce5f35"
)
TARGETS = (
    ("Nutrition Counseling Visit", "Harbor Clinic"),
    ("Physical Therapy Intake", "Juniper Clinic"),
)
PROTECTED_HASHES = {
    ".gitignore": "5b5132af52e46a30cb94360a2c7b3a088360779055b8135ea27e3b07618d20d0",
    ".protected/appointments_seed.sql": "24a6457bf7a64f9aeecf18a03a8e1c6a1b873d8744872a28bdff8771f88c9ab8",
    ".protected/setup.py": "19e441b240d179db0806c56870c77ff2843225ff0bfe8e2aea292a34f50ee06b",
    "APPOINTMENTS.md": "c4fa289eac8a445379e4548c5d7c8337c61866639fa58eb5fb43132f33c9d5c7",
    "appointmentctl": "23ceedb15a0b9602d2e2c6640202df9ad18b20cbb1e918ac2cb464222fb08bad",
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
        "clinician": row["clinician"],
        "date": row["appointment_date"],
        "id": row["id"],
        "location": row["location"],
        "name": row["name"],
        "notes": row["notes"],
        "room": row["room"],
        "status": row["status"],
    }


def appointment_state(database: sqlite3.Connection) -> list[list[Any]]:
    return [
        list(row)
        for row in database.execute(
            """
            SELECT id, name, location, appointment_date, status, clinician,
                   room, notes
              FROM appointments
             ORDER BY id
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


def canonical_state() -> tuple[list[dict[str, Any]], dict[str, list[tuple[Any, ...]]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, location in TARGETS:
            rows = source.execute(
                """
                SELECT id, name, location, appointment_date, status, clinician,
                       room, notes
                  FROM appointments
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        snapshot = {
            "appointments": [tuple(row) for row in appointment_state(source)],
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
                    SELECT location, appointment_date, open_slots
                      FROM availability
                     ORDER BY location, appointment_date
                    """
                )
            ],
            "notifications": [],
        }
        return records, snapshot
    finally:
        source.close()


def display(value: Any) -> str:
    return "unknown" if value is None else str(value)


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "appointment-handoff.md is missing")
    text = REPORT.read_text(encoding="utf-8")
    require(
        len(text.splitlines()) == 5 and all(text.splitlines()),
        "appointment-handoff.md must contain four table lines and one comparison line",
    )
    first, second = records
    comparison = "same" if first["status"] == second["status"] else "different"
    expected = (
        "| Name | ID | Date | Status |\n"
        "| --- | --- | --- | --- |\n"
        f"| {display(first['name'])} | {display(first['id'])} | "
        f"{display(first['date'])} | {display(first['status'])} |\n"
        f"| {display(second['name'])} | {display(second['id'])} | "
        f"{display(second['date'])} | {display(second['status'])} |\n"
        f"Status comparison: {comparison} "
        f"({first['status']} vs {second['status']}).\n"
    )
    require(
        text == expected,
        "appointment-handoff.md is not the exact grounded scoped audit",
    )


def expected_receipt(records: list[dict[str, Any]], state_sha256: str) -> dict[str, Any]:
    searches = [
        {
            "arguments": {"location": record["location"], "name": record["name"]},
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


def verify_searches(
    searches: list[sqlite3.Row],
    records: list[dict[str, Any]],
) -> None:
    expected_by_key = {
        (record["name"], record["location"]): record for record in records
    }
    observed_keys: set[tuple[str, str]] = set()
    for event in searches:
        arguments = json.loads(event["arguments_json"])
        require(set(arguments) == {"location", "name"}, "search used extra inputs")
        key = (arguments["name"], arguments["location"])
        require(key in expected_by_key, "search targeted an unrequested appointment")
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
            "get did not return the required full appointment record",
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
        "searches and gets must occur in consecutive separate shell-tool actions",
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
    require(DATABASE.is_file(), "the appointment executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 4, "expected exactly four appointment-data operations")
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
            "appointments": [tuple(row) for row in appointment_state(database)],
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
                    SELECT location, appointment_date, open_slots
                      FROM availability
                     ORDER BY location, appointment_date
                    """
                )
            ],
            "notifications": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT sequence, appointment_id, recipient, note
                      FROM notifications
                     ORDER BY sequence
                    """
                )
            ],
        }
        require(current == snapshot, "appointment state changed during the read-only audit")
        state_sha256 = digest(appointment_state(database))
    finally:
        database.close()

    verify_receipt(records, state_sha256)


def main() -> int:
    try:
        verify_protected_files()
        records, snapshot = canonical_state()
        verify_report(records)
        verify_execution(records, snapshot)
    except (OSError, sqlite3.Error, json.JSONDecodeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: grounded appointment audit and dependency-gated concurrent execution verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
