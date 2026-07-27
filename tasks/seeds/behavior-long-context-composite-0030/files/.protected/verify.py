#!/usr/bin/env python3
"""Protected verifier for the exact dependent meeting-register lookup."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".meeting-runtime" / "meetings.sqlite3"
SEED = ROOT / ".protected" / "meetings_seed.sql"
REPORT = ROOT / "supplier-policy-review.md"
TARGET_NAME = "Supplier Policy Review"
TARGET_LOCATION = "Beacon"
REQUIRED_STATUS = "active"
EXPECTED_FILE_HASHES = {
    ".gitignore": "90c70f5e2f65c5ba3644bd0b57d9aa8f5ed71bddd30e523c50864fe3e085f77f",
    "MEETING_REGISTER_GUIDE.md": "9aaa4caba9f027b73f620cf5b91b42c25c3bfcd0973b0c4088409eb8678cb3b1",
    "meetingctl": "df7b0b37e389348855a037eeb789f5ea53eaaa6df69f7c068472f21a0d10c937",
    ".protected/meetings_seed.sql": "d5e8ff740167f50917adab09e8cd3f0bb69dbfc983cb416fe24abcba50bda71f",
    ".protected/setup.py": "10f39703c5654a1b4c65e45f405fcf54d82c60bcf0755fa91427af6a063f2354",
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
        "date": row["meeting_date"],
    }


def state_snapshot(database: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "meetings": [
            tuple(row)
            for row in database.execute(
                """
                SELECT stable_id, name, location, status, meeting_date
                  FROM meetings
                 ORDER BY stable_id
                """
            )
        ],
        "notifications": [
            tuple(row)
            for row in database.execute(
                """
                SELECT sequence, meeting_id, message
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
            SELECT stable_id, name, location, status, meeting_date
              FROM meetings
             WHERE name = ? AND location = ?
             ORDER BY stable_id
            """,
            (TARGET_NAME, TARGET_LOCATION),
        ).fetchall()
        require(len(rows) == 1, "protected target must resolve uniquely")
        target = record_from_row(rows[0])
        require(
            target["status"] == REQUIRED_STATUS,
            "protected target must have the required status",
        )
        return target, state_snapshot(source)
    finally:
        source.close()


def expected_report(target: dict[str, Any]) -> str:
    return (
        f"- Stable ID: {target['id']}\n"
        f"- Status: {target['status']}\n"
        f"- Location/date: {target['location']} / {target['date']}\n"
    )


def verify_report(target: dict[str, Any]) -> None:
    require(REPORT.is_file(), "supplier-policy-review.md is missing")
    require(
        REPORT.read_text(encoding="utf-8") == expected_report(target),
        "supplier-policy-review.md is not the exact detail-grounded report",
    )


def verify_execution(
    target: dict[str, Any],
    expected_state: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the genuine meeting environment was not used")
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
            "meeting operation sequence is not pristine",
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
            "a forbidden, failed, or incomplete meeting operation was recorded",
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
            "search did not return the unique active requested meeting",
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
            "get did not return the requested complete meeting record",
        )
        require(
            int(search["finished_ns"]) < int(get["started_ns"]),
            "get started before the search response was available",
        )
        require(
            all(
                int(event["pid"]) == int(event["process_group_id"])
                and int(event["session_id"]) == int(event["pid"])
                for event in events
            )
            and int(search["process_group_id"]) != int(get["process_group_id"]),
            "search and get were not separate direct Bash-tool actions",
        )
        require(
            state_snapshot(database) == expected_state,
            "meeting or notification state changed",
        )
    finally:
        database.close()


def verify_workspace() -> None:
    allowed_root_names = {
        ".agents",
        ".codex",
        ".git",
        ".gitignore",
        ".meeting-runtime",
        ".protected",
        "MEETING_REGISTER_GUIDE.md",
        "meetingctl",
        "supplier-policy-review.md",
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
    print(
        "PASS: exact dependent meeting lookup, three-bullet report, "
        "and read-only state verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
