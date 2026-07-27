#!/usr/bin/env python3
"""Protected deterministic verification for the dependent course resolution."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROTECTED = ROOT / ".protected"
SEED_PATH = PROTECTED / "courses.json"
KEY_PATH = PROTECTED / "audit.key"
CLIENT_PATH = ROOT / "coursedesk"
GUIDE_PATH = ROOT / "COURSE_REGISTER_GUIDE.md"
GITIGNORE_PATH = ROOT / ".gitignore"
RUNTIME = ROOT / ".course-runtime"
DATABASE = RUNTIME / "register.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
HELP_EVIDENCE = RUNTIME / "help.json"
DELIVERABLE = ROOT / "course-resolution.md"
REFERENCE_DRIVER = ROOT / "reference_driver.py"

TARGET_NAME = "Applied Microeconomics"
TARGET_LOCATION = "Downtown"
REQUIRED_STATUS = "active"
BASE_FIELDS = ("id", "name", "location", "status", "date")
ALLOWED_FIELDS = {
    "id",
    "name",
    "location",
    "status",
    "date",
    "department",
    "instructor",
    "delivery",
    "credits",
}
EVENT_FIELDS = {
    "version",
    "sequence",
    "action",
    "request",
    "started_ns",
    "finished_ns",
    "pid",
    "parent_pid",
    "action_id",
    "result_sha256",
    "result_count",
    "sole_id",
    "seed_sha256",
    "state_sha256",
    "success",
    "violation",
    "error",
    "signature",
}
HELP_FIELDS = {
    "version",
    "action",
    "started_ns",
    "finished_ns",
    "pid",
    "parent_pid",
    "action_id",
    "seed_sha256",
    "client_sha256",
    "signature",
}

SEED_SHA256 = "d5efbb09d999975336400b29002ab37d453903c20247505d22d8c90937cbb04b"
KEY_SHA256 = "c1b9d2c8242b9a0f72572979f59783bb5a6be9d658e27992b8ea8b4c59c82332"
CLIENT_SHA256 = "8609db96fe8be13cc38b0a38b380b8071d5213f9638f6c7cfc543c80e3bd6b79"
GUIDE_SHA256 = "87e12f4ce4c839f481b4f08ba44c067f87154d78945a60581492aabd28d0c049"
GITIGNORE_SHA256 = "d86a90fafbfc3465e80953ee92266d0f049124013fd8f0a43adbad80fd36972d"
REFERENCE_DRIVER_SHA256 = "efb34d85e85db2be1ac3c9f92243cd85b4eb83498a2a4d676cbcf4582ce40807"


class VerificationError(RuntimeError):
    """A deterministic verification failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise VerificationError(f"cannot read protected task file: {error}") from error


def verify_static_files() -> None:
    require(file_sha256(SEED_PATH) == SEED_SHA256, "protected course-register seed changed")
    require(file_sha256(KEY_PATH) == KEY_SHA256, "protected evidence key changed")
    require(file_sha256(CLIENT_PATH) == CLIENT_SHA256, "course executable changed")
    require(file_sha256(GUIDE_PATH) == GUIDE_SHA256, "course register guide changed")
    require(file_sha256(GITIGNORE_PATH) == GITIGNORE_SHA256, ".gitignore changed")


def load_seed() -> list[dict[str, str]]:
    try:
        payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"protected course-register seed is unreadable: {error}") from error
    require(isinstance(payload, dict), "protected course-register seed is not an object")
    require(payload.get("schema_version") == 1, "protected course-register seed schema changed")
    records = payload.get("records")
    require(isinstance(records, list) and bool(records), "protected course-register seed is empty")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in records:
        require(isinstance(item, dict), "protected course record is not an object")
        require(
            set(BASE_FIELDS).issubset(item) and set(item).issubset(ALLOWED_FIELDS),
            "protected course record shape changed",
        )
        require(
            all(isinstance(value, str) for value in item.values()),
            "protected course record contains a non-string value",
        )
        record = dict(item)
        require(record["id"] not in seen, "protected course-register seed has duplicate IDs")
        seen.add(record["id"])
        normalized.append(record)
    require(len(normalized) >= 40, "protected noisy register is too small")
    return normalized


def hamming_distance(left: str, right: str) -> int | None:
    if len(left) != len(right):
        return None
    return sum(a != b for a, b in zip(left, right))


def find_target(records: list[dict[str, str]]) -> dict[str, str]:
    matches = [
        record
        for record in records
        if record["name"] == TARGET_NAME and record["location"] == TARGET_LOCATION
    ]
    require(len(matches) == 1, "protected target does not resolve uniquely")
    target = matches[0]
    require(target["status"] == REQUIRED_STATUS, "protected target is not active")
    require(
        any(
            record["name"] == TARGET_NAME
            and record["location"] != TARGET_LOCATION
            for record in records
        ),
        "same-name location distractors are missing",
    )
    require(
        any(
            record["location"] == TARGET_LOCATION
            and record["name"] != TARGET_NAME
            and "Microeconomics" in record["name"]
            for record in records
        ),
        "near-name Downtown distractors are missing",
    )
    require(
        any(
            record["id"] != target["id"]
            and hamming_distance(record["id"], target["id"]) == 1
            for record in records
        ),
        "similar stable-ID distractor is missing",
    )
    return target


def expected_state(records: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "records": sorted(records, key=lambda record: record["id"]),
        "notifications": [],
    }


def verify_runtime_shape() -> None:
    require(RUNTIME.is_dir(), "course-register runtime is missing")
    entries = {path.name for path in RUNTIME.iterdir()}
    require(
        entries == {"register.sqlite3", "audit.jsonl", "help.json", ".lock"},
        "course-register runtime contains missing or unexpected artifacts",
    )


def verify_database(records: list[dict[str, str]]) -> None:
    require(DATABASE.is_file(), "course-register runtime database is missing")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        rows = connection.execute(
            """
            SELECT id, name, location, status, date, detail_json
            FROM courses
            ORDER BY id
            """
        ).fetchall()
        actual_records: list[dict[str, str]] = []
        for row in rows:
            detail = json.loads(row["detail_json"])
            require(isinstance(detail, dict), "runtime detail record is malformed")
            require(
                (
                    row["id"],
                    row["name"],
                    row["location"],
                    row["status"],
                    row["date"],
                )
                == tuple(detail[field] for field in BASE_FIELDS),
                "runtime course columns disagree with full detail",
            )
            actual_records.append(detail)
        notifications = connection.execute(
            """
            SELECT sequence, course_id, recipient, message
            FROM notifications
            ORDER BY sequence
            """
        ).fetchall()
        marker_rows = connection.execute(
            "SELECT key, value FROM register_meta ORDER BY key"
        ).fetchall()
    except (sqlite3.Error, json.JSONDecodeError, KeyError) as error:
        raise VerificationError(f"course-register runtime database is invalid: {error}") from error
    finally:
        if connection is not None:
            connection.close()

    require(integrity == "ok", "course-register runtime database failed integrity check")
    require(
        actual_records == sorted(records, key=lambda record: record["id"]),
        "course records changed or runtime state is not authoritative",
    )
    require(not notifications, "a course notification was sent")
    require(
        [(row["key"], row["value"]) for row in marker_rows]
        == [("seed_sha256", SEED_SHA256)],
        "course-register seed marker changed",
    )


def read_events() -> list[dict[str, Any]]:
    require(AUDIT.is_file(), "signed course-register execution evidence is missing")
    try:
        lines = AUDIT.read_text(encoding="utf-8").splitlines()
        key = KEY_PATH.read_bytes().strip()
    except (OSError, UnicodeDecodeError) as error:
        raise VerificationError(f"cannot read signed execution evidence: {error}") from error
    require(len(lines) == 2 and all(lines), "expected exactly two course-register data operations")

    events: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(f"execution evidence line {index} is invalid") from error
        require(isinstance(event, dict), f"evidence line {index} is not an object")
        require(set(event) == EVENT_FIELDS, f"evidence line {index} shape changed")
        require(event["version"] == 1, f"evidence line {index} version changed")
        require(event["sequence"] == index, "course-register operation sequence is invalid")
        signature = event["signature"]
        require(isinstance(signature, str), f"evidence line {index} has no signature")
        unsigned = dict(event)
        del unsigned["signature"]
        expected_signature = hmac.new(
            key,
            canonical(unsigned),
            hashlib.sha256,
        ).hexdigest()
        require(
            hmac.compare_digest(signature, expected_signature),
            f"evidence line {index} is not authentic",
        )
        events.append(event)
    return events


def read_help_event() -> dict[str, Any]:
    require(HELP_EVIDENCE.is_file(), "signed built-in-help evidence is missing")
    try:
        event = json.loads(HELP_EVIDENCE.read_text(encoding="utf-8"))
        key = KEY_PATH.read_bytes().strip()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"signed built-in-help evidence is invalid: {error}") from error
    require(isinstance(event, dict), "built-in-help evidence is not an object")
    require(set(event) == HELP_FIELDS, "built-in-help evidence shape changed")
    require(
        event["version"] == 1 and event["action"] == "help",
        "built-in-help evidence action changed",
    )
    require(
        isinstance(event["started_ns"], int)
        and isinstance(event["finished_ns"], int)
        and event["started_ns"] <= event["finished_ns"],
        "built-in-help evidence timing is invalid",
    )
    require(
        isinstance(event["pid"], int)
        and isinstance(event["parent_pid"], int)
        and isinstance(event["action_id"], str)
        and bool(event["action_id"]),
        "built-in-help evidence process identity is invalid",
    )
    signature = event["signature"]
    require(isinstance(signature, str), "built-in-help evidence has no signature")
    unsigned = dict(event)
    del unsigned["signature"]
    expected_signature = hmac.new(
        key,
        canonical(unsigned),
        hashlib.sha256,
    ).hexdigest()
    require(
        hmac.compare_digest(signature, expected_signature),
        "built-in-help evidence is not authentic",
    )
    require(
        event["seed_sha256"] == SEED_SHA256
        and event["client_sha256"] == CLIENT_SHA256,
        "built-in-help evidence does not match this course register",
    )
    return event


def verify_events(
    help_event: dict[str, Any],
    events: list[dict[str, Any]],
    records: list[dict[str, str]],
    target: dict[str, str],
) -> None:
    search_result = {
        "match_count": 1,
        "matches": [
            {
                "id": target["id"],
                "name": target["name"],
                "location": target["location"],
            }
        ],
    }
    details_result = {"record": target}
    state_sha256 = digest(expected_state(records))

    search, details = events
    require(
        help_event["action_id"] != search["action_id"]
        and search["pid"] != help_event["parent_pid"]
        and help_event["finished_ns"] < search["started_ns"],
        "built-in help was not inspected in an earlier execution-tool action",
    )
    require(search["action"] == "search", "first course-register operation was not search")
    require(
        search["request"] == {"name": TARGET_NAME, "location": TARGET_LOCATION},
        "search was not jointly constrained to the exact name and location",
    )
    require(
        search["success"] is True
        and search["violation"] is False
        and search["error"] is None,
        "exact search failed or violated resolution scope",
    )
    require(
        search["result_count"] == 1
        and search["sole_id"] == target["id"]
        and search["result_sha256"] == digest(search_result),
        "search did not return exactly the unique requested course identity",
    )

    require(details["action"] == "details", "second course-register operation was not details")
    require(
        details["request"] == {"id": search["sole_id"]},
        "full retrieval did not use only the stable ID returned by search",
    )
    require(
        details["success"] is True
        and details["violation"] is False
        and details["error"] is None,
        "full-record retrieval failed or violated resolution scope",
    )
    require(
        details["result_count"] == 1
        and details["sole_id"] == target["id"]
        and details["result_sha256"] == digest(details_result),
        "details did not return the authoritative requested course record",
    )
    require(
        isinstance(search["action_id"], str)
        and isinstance(details["action_id"], str)
        and search["action_id"] != details["action_id"],
        "search and retrieval were not separate execution-tool actions",
    )
    require(
        isinstance(search["finished_ns"], int)
        and isinstance(details["started_ns"], int)
        and search["finished_ns"] < details["started_ns"],
        "full retrieval began before the search result was available",
    )

    for event in events:
        require(event["seed_sha256"] == SEED_SHA256, "evidence seed digest changed")
        require(event["state_sha256"] == state_sha256, "course-register state changed")


def verify_deliverable(target: dict[str, str]) -> None:
    require(DELIVERABLE.is_file(), "course-resolution.md is missing")
    expected = (
        f"- Stable ID: {target['id']}\n"
        f"- Status: {target['status']}\n"
        f"- Location/date: {target['location']} / {target['date']}\n"
    )
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise VerificationError(f"course-resolution.md is unreadable: {error}") from error
    require(
        actual == expected,
        "course-resolution.md is not the exact full-record-grounded three-bullet report",
    )


def verify_workspace_shape() -> None:
    allowed = {
        ".agents",
        ".codex",
        ".git",
        ".gitignore",
        ".protected",
        ".course-runtime",
        ".sandbox-home",
        "COURSE_REGISTER_GUIDE.md",
        "coursedesk",
        "course-resolution.md",
        "reference_driver.py",
    }
    unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    require(not unexpected, f"unexpected scratch artifacts remain: {unexpected}")
    protected_entries = {path.name for path in PROTECTED.iterdir()}
    require(
        protected_entries == {"courses.json", "audit.key", "verify.py"},
        "protected directory contains missing or unexpected artifacts",
    )
    if REFERENCE_DRIVER.exists():
        require(
            REFERENCE_DRIVER.is_file()
            and file_sha256(REFERENCE_DRIVER) == REFERENCE_DRIVER_SHA256,
            "reference setup driver changed",
        )
    require(
        not any(path.is_symlink() for path in ROOT.rglob("*")),
        "workspace contains an unexpected symbolic link",
    )


def main() -> int:
    try:
        verify_static_files()
        records = load_seed()
        target = find_target(records)
        verify_runtime_shape()
        verify_database(records)
        help_event = read_help_event()
        events = read_events()
        verify_events(help_event, events, records, target)
        verify_deliverable(target)
        verify_workspace_shape()
    except VerificationError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print(
        "verification passed: exact search, dependent full record, "
        "read-only state, and three-bullet output"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
