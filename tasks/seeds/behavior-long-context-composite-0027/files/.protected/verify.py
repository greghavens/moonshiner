#!/usr/bin/env python3
"""Protected deterministic verifier for the one-item inventory audit."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".inventory-runtime" / "inventory.sqlite3"
SEED = ROOT / ".protected" / "inventory_seed.sql"
REPORT = ROOT / "inventory-audit.md"
TARGET_NAME = "Archival Packing Tape"
TARGET_LOCATION = "Warehouse C"
EXPECTED_FILE_HASHES = {
    "INVENTORY_OPERATIONS.md": "f96af9d70bbda4538a79559f445b9c90bfa3539cd1663c368ebce599470cb8a5",
    "inventoryctl": "abb2de32c614a4b999773e5d3291d53243257c93386357d75a8e1ba11b473687",
    ".protected/inventory_seed.sql": "f1180fbd6137f17360b0446714d049da6d2d47ad0d0e7dc89798e81207f0bd57",
    ".protected/setup.py": "df35ede0bfa8b96a64c56af2ed83fbce6fadf6197d1bdcf74ea01f7135e85e0a",
    ".gitignore": "73f0aaca3c3dd483dad830ba72d6543be482f47e010cdf6c3568a642defc8f84",
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
    record = {
        "id": row["stable_id"],
        "name": row["name"],
        "location": row["location"],
        "status": row["status"],
        "category": row["category"],
        "unit": row["unit"],
        "quantity": row["quantity"],
        "bin": row["bin"],
        "retention_class": row["retention_class"],
        "handling_notes": row["handling_notes"],
    }
    if row["item_date"] is not None:
        record["date"] = row["item_date"]
    return record


def snapshot(database: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "inventory": [
            tuple(row)
            for row in database.execute(
                """
                SELECT stable_id, name, location, status, item_date,
                       category, unit, quantity, bin, retention_class,
                       handling_notes
                  FROM inventory
                 ORDER BY stable_id
                """
            )
        ],
        "notifications": [
            tuple(row)
            for row in database.execute(
                """
                SELECT sequence, item_id, message
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
            SELECT stable_id, name, location, status, item_date, category,
                   unit, quantity, bin, retention_class, handling_notes
              FROM inventory
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
    requested = (
        ("Stable ID", "id"),
        ("Name", "name"),
        ("Location", "location"),
        ("Status", "status"),
        ("Date", "date"),
    )
    return "".join(
        f"{label}: {target[key] if key in target else 'unknown'}\n"
        for label, key in requested
    )


def verify_report(target: dict[str, Any]) -> None:
    require(
        REPORT.is_file() and not REPORT.is_symlink(),
        "inventory-audit.md is missing or is not a regular deliverable",
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected_report(target),
        "inventory-audit.md is not the exact detail-grounded report",
    )


def verify_execution(
    target: dict[str, Any],
    expected_snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the genuine inventory environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM execution_journal ORDER BY sequence"
        ).fetchall()
        help_events = [event for event in events if event["operation"] == "help"]
        data_events = [event for event in events if event["operation"] != "help"]
        require(
            len(help_events) >= 1,
            "top-level inventory help was not inspected before inventory data",
        )
        require(len(data_events) == 2, "expected exactly one search and one get")
        search, get = data_events
        require(
            any(int(event["sequence"]) < int(search["sequence"]) for event in help_events),
            "top-level inventory help must be inspected before inventory data",
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
            "a forbidden, failed, or incomplete inventory operation was recorded",
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
            "search did not return the unique active requested item",
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
            "get did not return the requested full inventory record",
        )
        require(
            int(search["finished_ns"]) < int(get["started_ns"]),
            "get started before the search result was available",
        )

        require(
            snapshot(database) == expected_snapshot,
            "inventory or notification state changed",
        )
    finally:
        database.close()


def verify_workspace() -> None:
    allowed_root_names = {
        ".agents",
        ".codex",
        ".git",
        ".gitignore",
        ".inventory-runtime",
        ".protected",
        "INVENTORY_OPERATIONS.md",
        "inventory-audit.md",
        "inventoryctl",
    }
    extras = sorted(
        path.name for path in ROOT.iterdir() if path.name not in allowed_root_names
    )
    require(not extras, f"unexpected scratch artifacts remain: {', '.join(extras)}")

    protected_names = {path.name for path in (ROOT / ".protected").iterdir()}
    expected_protected_names = {
        "inventory_seed.sql",
        "setup.py",
        "verify.py",
    }
    protected_extras = sorted(protected_names - expected_protected_names)
    require(
        not protected_extras,
        f"unexpected protected scratch artifacts remain: {', '.join(protected_extras)}",
    )

    runtime_names = {path.name for path in (ROOT / ".inventory-runtime").iterdir()}
    allowed_runtime_names = {
        "inventory.sqlite3",
        "inventory.sqlite3-shm",
        "inventory.sqlite3-wal",
    }
    runtime_extras = sorted(runtime_names - allowed_runtime_names)
    require(
        not runtime_extras,
        f"unexpected runtime scratch artifacts remain: {', '.join(runtime_extras)}",
    )


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
