#!/usr/bin/env python3
"""Protected deterministic verifier for the dependent CRM account lookup."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".crm-runtime" / "accounts.sqlite3"
SEED = ROOT / ".protected" / "accounts_seed.sql"
REPORT = ROOT / "account-details.md"
TARGET_NAME = "Arbor Foods Renewal"
TARGET_LOCATION = "West"
REQUIRED_STATUS = "active"
EXPECTED_FILE_HASHES = {
    ".gitignore": "3b4203cbb6c0d76f1e8776ef0126e40d92066320d9e053d3592427de84de6d56",
    "CRM_ACCOUNT_GUIDE.md": "a211d95f2fd8cd83e5c11c120e70108c7c27a1a3ef4686113b5f85499c40c5f1",
    "crmctl": "22c0e150bb8e14d8fc261fde675e0739e42048667f766f7ae5dcaae96d356576",
    ".protected/accounts_seed.sql": "3edf9eb7625f57595c9dbdb9144fe9ac5312e824709c018c0ede01aa14b30225",
    ".protected/setup.py": "19dbd52bb56a7cdc63b081ae9dc6cf337c99af1c27cfccaaa003f2ff3f6adb41",
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


def record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["stable_id"],
        "name": row["name"],
        "location": row["location"],
        "status": row["status"],
        "date": row["account_date"],
    }


def state_snapshot(database: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "accounts": [
            tuple(row)
            for row in database.execute(
                """
                SELECT stable_id, name, location, status, account_date
                  FROM accounts
                 ORDER BY stable_id
                """
            )
        ],
        "notifications": [
            tuple(row)
            for row in database.execute(
                """
                SELECT sequence, account_id, message
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
            SELECT stable_id, name, location, status, account_date
              FROM accounts
             WHERE name = ? AND location = ?
             ORDER BY stable_id
            """,
            (TARGET_NAME, TARGET_LOCATION),
        ).fetchall()
        require(len(rows) == 1, "protected target must resolve uniquely")
        target = record_from_row(rows[0])
        require(target["status"] == REQUIRED_STATUS, "protected target must be active")
        return target, state_snapshot(source)
    finally:
        source.close()


def expected_report(target: dict[str, Any]) -> str:
    fields = (
        ("Stable ID", "id"),
        ("Name", "name"),
        ("Location", "location"),
        ("Status", "status"),
        ("Date", "date"),
    )
    return "".join(f"{label}: {target[key]}\n" for label, key in fields)


def verify_report(target: dict[str, Any]) -> None:
    require(REPORT.is_file(), "account-details.md is missing")
    require(
        REPORT.read_text(encoding="utf-8") == expected_report(target),
        "account-details.md is not the exact complete-record-grounded report",
    )


def verify_execution(
    target: dict[str, Any],
    expected_state: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the genuine CRM environment was not used")
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
            "account operation sequence is not pristine",
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
            "a forbidden, failed, or incomplete account operation was recorded",
        )

        search_arguments = json.loads(search["arguments_json"])
        require(
            search_arguments == {"location": TARGET_LOCATION, "name": TARGET_NAME},
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
            "search did not return the unique active requested account",
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
            "get did not return the requested complete account record",
        )
        require(
            int(search["finished_ns"]) < int(get["started_ns"]),
            "get started before the search response was available",
        )
        require(
            int(search["parent_pid"]) != int(get["parent_pid"]),
            "search and get were combined in one direct Bash-tool action",
        )
        require(
            state_snapshot(database) == expected_state,
            "account or notification state changed",
        )
    finally:
        database.close()


def verify_workspace() -> None:
    allowed_root_names = {
        ".agents",
        ".codex",
        ".crm-runtime",
        ".git",
        ".gitignore",
        ".protected",
        "CRM_ACCOUNT_GUIDE.md",
        "account-details.md",
        "crmctl",
        "reference_driver.py",
    }
    extras = sorted(
        path.name for path in ROOT.iterdir() if path.name not in allowed_root_names
    )
    require(not extras, f"unexpected scratch artifacts remain: {', '.join(extras)}")


def main() -> int:
    try:
        verify_protected_files()
        target, expected_state = canonical_target()
        verify_report(target)
        verify_execution(target, expected_state)
        verify_workspace()
    except (
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        VerificationError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact dependent CRM lookup, grounded report, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
