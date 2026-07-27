#!/usr/bin/env python3
"""Protected deterministic verifier for the case-register reconciliation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".case-runtime" / "cases.sqlite3"
SEED = ROOT / ".protected" / "cases_seed.sql"
REPORT = ROOT / "case-register.md"
TARGET_NAME = "Duplicate Subscription Charge"
TARGET_LOCATION = "Acme"
EXPECTED_FILE_HASHES = {
    "CASE_REGISTER_GUIDE.md": "480f48d08172bf1aafc2383538856e77dfa59e3b24c4118d4e0e544980d0e6ee",
    "casectl": "4a098710754a39d8619c4cf6d315fece4af2b16d927e9c52bf2ddc5d10056a50",
    ".protected/cases_seed.sql": "486ee934cff2098b332dd1a5425f2f4e4f352c4cafe291224be2da5e1ba10257",
    ".protected/setup.py": "9f5c65ecf0cfdef357d86fbc9f2e5d748527a0c86605564b8dceedd25585f5d6",
    ".gitignore": "3f94f5e0da41d543c9448cec26353336ae8b2d01b4029a89d5b02823ff579e0d",
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
        "date": row["case_date"],
        "priority": row["priority"],
        "owner": row["owner"],
        "source": row["source"],
        "description": row["description"],
    }


def snapshot(database: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "cases": [
            tuple(row)
            for row in database.execute(
                """
                SELECT stable_id, name, location, status, case_date, priority,
                       owner, source, description
                  FROM cases
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
                SELECT case_id, service_date, available
                  FROM availability
                 ORDER BY case_id, service_date
                """
            )
        ],
        "notifications": [
            tuple(row)
            for row in database.execute(
                """
                SELECT sequence, case_id, message
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
            SELECT stable_id, name, location, status, case_date, priority,
                   owner, source, description
              FROM cases
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
        "| Field | Value |\n"
        "|---|---|\n"
        f"| ID | {target['id']} |\n"
        f"| name | {target['name']} |\n"
        f"| location | {target['location']} |\n"
        f"| status | {target['status']} |\n"
        f"| date | {target['date']} |\n"
    )


def verify_report(target: dict[str, Any]) -> None:
    require(REPORT.is_file(), "case-register.md is missing")
    require(
        REPORT.read_text(encoding="utf-8") == expected_report(target),
        "case-register.md is not the exact detail-grounded two-column table",
    )


def verify_execution(
    target: dict[str, Any],
    expected_snapshot: dict[str, list[tuple[Any, ...]]],
) -> bool:
    require(DATABASE.is_file(), "the genuine case-register environment was not used")
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
            "case operation sequence is not pristine",
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
            "a forbidden, failed, or incomplete case operation was recorded",
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
            "search did not return the unique active requested case",
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
            "get did not return the requested full case record",
        )
        require(
            int(search["finished_ns"]) < int(get["started_ns"]),
            "get started before the search result was available",
        )

        metadata = database.execute(
            "SELECT reference_mode FROM runtime_metadata WHERE singleton = 1"
        ).fetchone()
        require(metadata is not None, "runtime metadata is missing")
        reference_mode = bool(int(metadata["reference_mode"]))
        if not reference_mode:
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
            "case, profile, availability, or notification state changed",
        )
        return reference_mode
    finally:
        database.close()


def verify_workspace(reference_mode: bool) -> None:
    allowed_root_names = {
        ".agents",
        ".case-runtime",
        ".codex",
        ".git",
        ".gitignore",
        ".protected",
        "CASE_REGISTER_GUIDE.md",
        "case-register.md",
        "casectl",
    }
    if reference_mode:
        allowed_root_names.add("reference_driver.py")
    extras = sorted(
        path.name for path in ROOT.iterdir() if path.name not in allowed_root_names
    )
    require(not extras, f"unexpected scratch artifacts remain: {', '.join(extras)}")

    protected_names = {path.name for path in (ROOT / ".protected").iterdir()}
    protected_extras = sorted(
        protected_names - {"cases_seed.sql", "setup.py", "verify.py"}
    )
    require(
        not protected_extras,
        "unexpected protected-file artifacts remain: "
        + ", ".join(protected_extras),
    )

    runtime_names = {path.name for path in (ROOT / ".case-runtime").iterdir()}
    runtime_extras = sorted(
        runtime_names
        - {"cases.sqlite3", "cases.sqlite3-shm", "cases.sqlite3-wal"}
    )
    require(
        not runtime_extras,
        "unexpected runtime artifacts remain: " + ", ".join(runtime_extras),
    )


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
    print("PASS: exact dependent lookup, grounded table, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
