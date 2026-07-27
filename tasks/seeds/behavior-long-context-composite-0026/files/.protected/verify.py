#!/usr/bin/env python3
"""Protected deterministic verifier for the order-register resolution."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".order-runtime" / "orders.sqlite3"
SEED = ROOT / ".protected" / "orders_seed.sql"
REPORT = ROOT / "resolved-order.txt"
TARGET_NAME = "Community Garden Supply Order"
TARGET_LOCATION = "Boise"
EXPECTED_FILE_HASHES = {
    ".gitignore": "82ee555a51bdc786b2d724ecdf2a0851ae84207ef0d46dd9371a2e39545fce3a",
    "ORDER_REGISTER_GUIDE.md": "b50d45f1df821f48379f7af94f74c59b5b9c74e1e9a4aaaa28b8c06947283fc3",
    "orderctl": "61712b148a9205414c9ff2747ef60d2376d3708226e7b2db9af7eede2523af5e",
    ".protected/orders_seed.sql": "cdcaffe2057789103b4016e40d6bb64a7e67ddf187aa3d6aae37147bbe5005ae",
    ".protected/setup.py": "234371c5afc2b1199cbd8bfe3470c34cb36b00a638f7ad73e7dec819d72d5169",
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
        "id": row["stable_id"],
        "name": row["name"],
        "location": row["location"],
        "status": row["status"],
        "date": row["order_date"],
        "requested_by": row["requested_by"],
        "warehouse": row["warehouse"],
        "item_count": row["item_count"],
        "notes": row["notes"],
    }


def snapshot(database: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "orders": [
            tuple(row)
            for row in database.execute(
                """
                SELECT stable_id, name, location, status, order_date,
                       requested_by, warehouse, item_count, notes
                  FROM orders
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
                SELECT order_id, service_date, available
                  FROM availability
                 ORDER BY order_id, service_date
                """
            )
        ],
        "notifications": [
            tuple(row)
            for row in database.execute(
                """
                SELECT sequence, order_id, message
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
            SELECT stable_id, name, location, status, order_date, requested_by,
                   warehouse, item_count, notes
              FROM orders
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


def expected_report(target: dict[str, Any]) -> str:
    return (
        f"Found: stable ID {target['id']} is the {target['status']} "
        f"{target['name']} in {target['location']}, dated {target['date']}.\n"
    )


def verify_report(target: dict[str, Any]) -> None:
    require(REPORT.is_file(), "resolved-order.txt is missing")
    require(
        REPORT.read_text(encoding="utf-8") == expected_report(target),
        "resolved-order.txt is not the exact full-detail-grounded sentence",
    )


def verify_execution(
    target: dict[str, Any],
    expected_snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the genuine order-register environment was not used")
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
            "order operation sequence is not pristine",
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
            "a forbidden, failed, or incomplete order operation was recorded",
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
                    "status": target["status"],
                }
            ]
        }
        require(
            search["result_count"] == 1
            and search["sole_id"] == target["id"]
            and search["result_digest"] == digest(search_result),
            "search did not return the unique active requested order",
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
            "get did not return the requested full order record",
        )
        require(
            int(search["finished_ns"]) < int(get["started_ns"]),
            "get started before the search result was available",
        )

        metadata = database.execute(
            "SELECT reference_mode FROM runtime_metadata WHERE singleton = 1"
        ).fetchone()
        require(metadata is not None, "runtime metadata is missing")
        if int(metadata["reference_mode"]) == 0:
            require(
                int(search["harness_action"]) != int(get["harness_action"]),
                "search and get were combined in one Bash-tool action",
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
            "order, profile, availability, or notification state changed",
        )
    finally:
        database.close()


def verify_workspace() -> None:
    allowed_root_names = {
        ".agents",
        ".codex",
        ".git",
        ".gitignore",
        ".order-runtime",
        ".protected",
        "ORDER_REGISTER_GUIDE.md",
        "orderctl",
        "reference_driver.py",
        "resolved-order.txt",
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
    print("PASS: scoped dependent lookup, grounded sentence, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
