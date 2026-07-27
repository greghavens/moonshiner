#!/usr/bin/env python3
"""Protected deterministic verifier for the dependent shipment lookup."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".shipment-runtime" / "shipments.sqlite3"
SEED = ROOT / ".protected" / "shipments_seed.sql"
REPORT = ROOT / "shipment-record.md"
TARGET_NAME = "Parcel Juniper"
TARGET_LOCATION = "Portland"
EXPECTED_FILE_HASHES = {
    "SHIPMENT_REGISTER_GUIDE.md": "51b0d4b5d784b0b4ac8125d1168526ea4307e108616ae25eeae9b7c89e01a6f5",
    "shipmentctl": "5859a11261a54a49517a5bf658f5c1ebf20a3b2231f00b9723c57a279ca6a680",
    ".protected/shipments_seed.sql": "88062eb03da2766a7354416997908fa7ecd311f01e0a89d789e09eac519791c4",
    ".protected/setup.py": "1019ca68855f8ec0c7c66372d654bcf455f4104b13ec897b4293aa4cdcfaa38e",
    ".gitignore": "266db835fbe669cf31ce4596e8267ccc5a3a1bc6dc0b88f8fe4a683a31d5c700",
}
REFERENCE_DRIVER_HASH = (
    "6e66f2a79485d5b31351b1ac253a81eeaa1e238bf2311576890abcefad1e0274"
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
        "id": row["stable_id"],
        "name": row["name"],
        "location": row["location"],
        "status": row["status"],
        "date": row["shipment_date"],
        "carrier": row["carrier"],
        "service_level": row["service_level"],
        "weight_kg": row["weight_kg"],
        "tracking_class": row["tracking_class"],
        "contact": row["contact"],
        "handling_notes": row["handling_notes"],
    }


def snapshot(database: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "shipments": [
            tuple(row)
            for row in database.execute(
                """
                SELECT stable_id, name, location, status, shipment_date, carrier,
                       service_level, weight_kg, tracking_class, contact,
                       handling_notes
                  FROM shipments
                 ORDER BY stable_id
                """
            )
        ],
        "notifications": [
            tuple(row)
            for row in database.execute(
                """
                SELECT sequence, shipment_id, message
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
            SELECT stable_id, name, location, status, shipment_date, carrier,
                   service_level, weight_kg, tracking_class, contact,
                   handling_notes
              FROM shipments
             WHERE name = ? AND location = ?
             ORDER BY stable_id
            """,
            (TARGET_NAME, TARGET_LOCATION),
        ).fetchall()
        require(len(rows) == 1, "protected target must resolve uniquely")
        return detail_record(rows[0]), snapshot(source)
    finally:
        source.close()


def expected_report(target: dict[str, Any]) -> str:
    requested = (
        ("Stable ID", "id"),
        ("Name", "name"),
        ("Location", "location"),
        ("Status", "status"),
        ("Date", "date"),
    )
    return "".join(f"{label}: {target[key]}\n" for label, key in requested)


def verify_report(target: dict[str, Any]) -> None:
    require(REPORT.is_file(), "shipment-record.md is missing")
    require(
        REPORT.read_text(encoding="utf-8") == expected_report(target),
        "shipment-record.md is not the exact complete-record-grounded report",
    )


def verify_execution(
    target: dict[str, Any],
    expected_snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the genuine shipment environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM execution_journal ORDER BY sequence"
        ).fetchall()
        help_inspections = database.execute(
            "SELECT * FROM help_inspections ORDER BY sequence"
        ).fetchall()
        require(len(events) == 2, "expected exactly one search and one get")
        search, get = events
        require(
            any(
                int(inspection["inspected_ns"]) < int(search["started_ns"])
                for inspection in help_inspections
            ),
            "built-in help was not inspected before shipment-data access",
        )
        require(
            [search["sequence"], get["sequence"]] == [1, 2],
            "shipment operation sequence is not pristine",
        )
        require(
            [search["operation"], get["operation"]] == ["search", "get"],
            "the exact search must be followed by the dependent get",
        )
        require(
            all(
                event["violation"] == 0
                and event["error"] is None
                and event["finished_ns"] is not None
                for event in events
            ),
            "a forbidden, failed, or incomplete shipment operation was recorded",
        )

        search_arguments = json.loads(search["arguments_json"])
        require(
            search_arguments
            == {
                "location": TARGET_LOCATION,
                "name": TARGET_NAME,
            },
            "search did not use both exact supplied fields",
        )
        search_result = {
            "matches": [
                {
                    "id": target["id"],
                    "name": target["name"],
                    "location": target["location"],
                }
            ]
        }
        require(
            search["result_count"] == 1
            and search["sole_id"] == target["id"]
            and search["result_digest"] == digest(search_result),
            "search did not return the unique requested shipment",
        )

        get_arguments = json.loads(get["arguments_json"])
        require(
            get_arguments == {"id": search["sole_id"]},
            "get did not use only the stable ID returned by search",
        )
        require(
            get["result_count"] == 1
            and get["sole_id"] == target["id"]
            and get["result_digest"] == digest({"record": target}),
            "get did not return the requested complete shipment record",
        )
        require(
            int(search["finished_ns"]) < int(get["started_ns"]),
            "get started before the search response was available",
        )
        require(
            int(search["session_id"]) != int(get["session_id"]),
            "search and get were combined in one direct Bash-tool action",
        )
        require(
            snapshot(database) == expected_snapshot,
            "shipment or notification state changed",
        )
    finally:
        database.close()


def verify_workspace() -> None:
    reference_driver = ROOT / "reference_driver.py"
    if reference_driver.exists():
        require(
            reference_driver.is_file()
            and file_digest(reference_driver) == REFERENCE_DRIVER_HASH,
            "unexpected scratch artifact remains: reference_driver.py",
        )
    allowed_root_names = {
        ".agents",
        ".codex",
        ".git",
        ".gitignore",
        ".protected",
        ".shipment-runtime",
        "SHIPMENT_REGISTER_GUIDE.md",
        "reference_driver.py",
        "shipment-record.md",
        "shipmentctl",
    }
    extras = sorted(
        path.name for path in ROOT.iterdir() if path.name not in allowed_root_names
    )
    require(not extras, f"unexpected scratch artifacts remain: {', '.join(extras)}")


def main() -> int:
    try:
        verify_protected_files()
        target, expected_snapshot = canonical_target()
        verify_report(target)
        verify_execution(target, expected_snapshot)
        verify_workspace()
    except (
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        VerificationError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact dependent lookup, grounded report, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
