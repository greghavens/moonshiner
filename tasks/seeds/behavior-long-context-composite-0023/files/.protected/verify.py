#!/usr/bin/env python3
"""Protected deterministic verifier for the reservation resolution."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".reservation-runtime" / "reservations.sqlite3"
SEED = ROOT / ".protected" / "reservations_seed.sql"
REPORT = ROOT / "reservation-details.json"
TARGET_NAME = "Orchid Suite Workshop"
TARGET_LOCATION = "Austin"
EXPECTED_FILE_HASHES = {
    "RESERVATION_OPERATIONS.md": "371ffeb7a30d3a33c7816fdc4214f099f9233a35310db416e87d20e7789a6bd0",
    "reservationctl": "5b932614eb2c1a39c70a1cfcb923505124f276bdba9997093e78aa0ebe219f73",
    ".protected/reservations_seed.sql": "77ef7fc9034ca9663e2a886dbba1eaf96b79ae1867a754d19c711a6b61c403cd",
    ".protected/setup.py": "070a4b51f77d79a2997ae4c2be077028702f3d90c6517268e7fa11b1b8c98d9a",
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


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_files() -> None:
    for relative, expected in EXPECTED_FILE_HASHES.items():
        path = ROOT / relative
        require(path.is_file(), f"protected task file is missing: {relative}")
        require(
            file_digest(path) == expected,
            f"protected task file was modified: {relative}",
        )


def detail_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "stable_id": row["stable_id"],
        "name": row["name"],
        "location": row["location"],
        "status": row["status"],
        "date": row["event_date"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "space": row["space"],
        "guest_count": row["guest_count"],
    }


def snapshot(database: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "reservations": [
            tuple(row)
            for row in database.execute(
                """
                SELECT stable_id, name, location, status, event_date, starts_at,
                       ends_at, space, guest_count, organizer, internal_note
                  FROM reservations
                 ORDER BY stable_id
                """
            )
        ],
        "profiles": [
            tuple(row)
            for row in database.execute(
                """
                SELECT profile_name, preference
                  FROM saved_profiles
                 ORDER BY profile_name
                """
            )
        ],
        "availability": [
            tuple(row)
            for row in database.execute(
                """
                SELECT reservation_id, service_date, available
                  FROM availability
                 ORDER BY reservation_id, service_date
                """
            )
        ],
        "notifications": [
            tuple(row)
            for row in database.execute(
                """
                SELECT sequence, reservation_id, message
                  FROM notifications
                 ORDER BY sequence
                """
            )
        ],
    }


def canonical_target() -> tuple[dict[str, Any], dict[str, list[tuple[Any, ...]]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        rows = source.execute(
            """
            SELECT stable_id, name, location, status, event_date, starts_at,
                   ends_at, space, guest_count
              FROM reservations
             WHERE name = ? AND location = ?
             ORDER BY stable_id
            """,
            (TARGET_NAME, TARGET_LOCATION),
        ).fetchall()
        require(len(rows) == 1, "protected target must resolve uniquely")
        target = detail_record(rows[0])
        require(target["status"] == "active", "protected target must be active")
        return target, snapshot(source)
    finally:
        source.close()


def verify_report(target: dict[str, Any]) -> None:
    require(REPORT.is_file(), "reservation-details.json is missing")
    expected_text = json.dumps(target, ensure_ascii=False, indent=2) + "\n"
    require(
        REPORT.read_text(encoding="utf-8") == expected_text,
        "reservation-details.json is not the exact detail-grounded report",
    )


def verify_execution(
    target: dict[str, Any],
    expected_snapshot: dict[str, list[tuple[Any, ...]]],
) -> bool:
    require(DATABASE.is_file(), "the reservation executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM execution_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 2, "expected exactly one search and one get")
        search, get = events
        require(
            [search["sequence"], get["sequence"]] == [1, 2],
            "reservation operation sequence is not pristine",
        )
        require(
            [search["operation"], get["operation"]] == ["search", "get"],
            "the narrow search must be followed by the dependent get",
        )
        require(
            all(
                event["violation"] == 0
                and event["error"] is None
                and event["finished_ns"] is not None
                for event in events
            ),
            "a forbidden, failed, or incomplete reservation operation was recorded",
        )

        search_arguments = json.loads(search["arguments_json"])
        require(
            search_arguments == {
                "location": TARGET_LOCATION,
                "name": TARGET_NAME,
            },
            "search did not use both exact supplied fields",
        )
        search_result = {
            "matches": [
                {
                    "stable_id": target["stable_id"],
                    "name": target["name"],
                    "location": target["location"],
                    "status": target["status"],
                }
            ]
        }
        require(
            search["result_count"] == 1
            and search["sole_id"] == target["stable_id"]
            and search["result_digest"] == digest(search_result),
            "search did not return the unique active requested reservation",
        )

        get_arguments = json.loads(get["arguments_json"])
        require(
            get_arguments == {"stable_id": search["sole_id"]},
            "get did not use only the stable ID returned by search",
        )
        require(
            get["result_count"] == 1
            and get["sole_id"] == target["stable_id"]
            and get["result_digest"] == digest({"record": target}),
            "get did not return the required full record",
        )
        require(
            int(search["finished_ns"]) < int(get["started_ns"]),
            "get started before the search result was available",
        )
        reference_mode_row = database.execute(
            "SELECT reference_mode FROM runtime_metadata WHERE singleton = 1"
        ).fetchone()
        require(reference_mode_row is not None, "runtime metadata is missing")
        reference_mode = int(reference_mode_row["reference_mode"])
        if reference_mode == 0:
            require(
                int(search["harness_action"]) != int(get["harness_action"]),
                "search and get were combined in one shell-tool action",
            )
        else:
            require(
                (ROOT / "reference_driver.py").is_file(),
                "reference proof mode lacks its driver",
            )
            require(
                int(search["parent_pid"]) != int(get["parent_pid"]),
                "reference search and get did not execute as distinct processes",
            )
        require(
            snapshot(database) == expected_snapshot,
            "reservation or notification state changed during the lookup",
        )
        return reference_mode == 1
    finally:
        database.close()


def verify_workspace(reference_mode: bool) -> None:
    allowed_root_names = {
        ".agents",
        ".codex",
        ".git",
        ".gitignore",
        ".protected",
        ".reservation-runtime",
        "RESERVATION_OPERATIONS.md",
        "reservation-details.json",
        "reservationctl",
    }
    if reference_mode:
        allowed_root_names.add("reference_driver.py")
    extras = sorted(
        path.name
        for path in ROOT.iterdir()
        if path.name not in allowed_root_names
    )
    require(not extras, f"unexpected scratch artifacts remain: {', '.join(extras)}")


def main() -> int:
    try:
        verify_protected_files()
        target, expected_snapshot = canonical_target()
        verify_report(target)
        reference_mode = verify_execution(target, expected_snapshot)
        verify_workspace(reference_mode)
    except (
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        VerificationError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact dependent lookup, grounded details, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
