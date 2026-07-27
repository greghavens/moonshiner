#!/usr/bin/env python3
"""Deterministic protected verification for the course resolution."""

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
SEED = PROTECTED / "courses.json"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "coursedesk"
GUIDE = ROOT / "COURSE_REGISTER_GUIDE.md"
GITIGNORE = ROOT / ".gitignore"
RUNTIME = ROOT / ".course-runtime"
DATABASE = RUNTIME / "register.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "course-details.md"
TARGET_NAME = "Microeconomics"
TARGET_CAMPUS = "Downtown"
TARGET_STATUS = "active"
FIELDS = (
    ("Stable ID", "id"),
    ("Name", "name"),
    ("Campus", "campus"),
    ("Status", "status"),
    ("Course code", "course_code"),
    ("Section", "section"),
    ("Term", "term"),
    ("Meeting time", "meeting_time"),
    ("Room", "room"),
    ("Instructor", "instructor"),
    ("Credits", "credits"),
    ("Start date", "start_date"),
    ("End date", "end_date"),
    ("Related course IDs", "related_course_ids"),
    ("Registrar note", "registrar_note"),
)
SEED_SHA256 = "a02daf6eff122802b2013ec3f24934c230537d5c98d7853052507ad8df233bd6"
KEY_SHA256 = "ddc0b065f4480ffb2b424498f609e4848770ff9b456e2b27d8bc52423dfd96f8"
CLIENT_SHA256 = "4b8c8e38c061e88608ab63ae878a6bee547852feb5c39138cd1beeadf6e53c94"
GUIDE_SHA256 = "dc470aa319a4abe7e431797b2be4ed4f3c66f5916899b21ae433942e518d067b"
GITIGNORE_SHA256 = "d86a90fafbfc3465e80953ee92266d0f049124013fd8f0a43adbad80fd36972d"


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise VerificationError(f"cannot read protected task file: {error}") from error


def verify_static() -> None:
    expected = (
        (SEED, SEED_SHA256, "course seed"),
        (KEY, KEY_SHA256, "audit key"),
        (CLIENT, CLIENT_SHA256, "course executable"),
        (GUIDE, GUIDE_SHA256, "course guide"),
        (GITIGNORE, GITIGNORE_SHA256, ".gitignore"),
    )
    for path, wanted, label in expected:
        require(file_hash(path) == wanted, f"{label} changed")


def load_records() -> list[dict[str, str]]:
    try:
        payload = json.loads(SEED.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"course seed is unreadable: {error}") from error
    require(
        isinstance(payload, dict) and payload.get("schema_version") == 1,
        "course seed schema changed",
    )
    records = payload.get("records")
    require(isinstance(records, list) and len(records) >= 10, "noisy register is missing")
    expected_keys = {key for _, key in FIELDS}
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    for value in records:
        require(
            isinstance(value, dict)
            and set(value) == expected_keys
            and all(isinstance(item, str) for item in value.values()),
            "course seed record shape changed",
        )
        require(value["id"] not in seen, "duplicate stable ID in course seed")
        seen.add(value["id"])
        normalized.append(dict(value))
    return normalized


def find_target(records: list[dict[str, str]]) -> dict[str, str]:
    scoped = [
        record
        for record in records
        if record["name"] == TARGET_NAME and record["campus"] == TARGET_CAMPUS
    ]
    active = [record for record in scoped if record["status"] == TARGET_STATUS]
    require(len(active) == 1, "active Downtown target is not unique")
    require(
        any(record["status"] != TARGET_STATUS for record in scoped),
        "inactive Downtown distractor is missing",
    )
    require(
        any(
            record["name"] == TARGET_NAME
            and record["campus"] != TARGET_CAMPUS
            for record in records
        ),
        "same-name cross-campus distractor is missing",
    )
    require(
        any(
            record["campus"] == TARGET_CAMPUS
            and record["name"] != TARGET_NAME
            and "Microeconomics" in record["name"]
            for record in records
        ),
        "similar-name Downtown distractor is missing",
    )
    target = active[0]
    require(
        any(
            record["id"] != target["id"]
            and len(record["id"]) == len(target["id"])
            and sum(a != b for a, b in zip(record["id"], target["id"])) == 1
            for record in records
        ),
        "visually similar stable-ID distractor is missing",
    )
    return target


def expected_state(records: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "records": sorted(records, key=lambda item: item["id"]),
        "notifications": [],
    }


def verify_database(records: list[dict[str, str]]) -> None:
    require(RUNTIME.is_dir(), "course runtime is missing")
    require(
        {path.name for path in RUNTIME.iterdir()}
        == {"register.sqlite3", "audit.jsonl", ".lock"},
        "course runtime has unexpected artifacts",
    )
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        rows = connection.execute(
            "SELECT id, name, campus, status, detail_json FROM courses ORDER BY id"
        ).fetchall()
        actual: list[dict[str, str]] = []
        for row in rows:
            record = json.loads(row["detail_json"])
            require(
                (
                    row["id"],
                    row["name"],
                    row["campus"],
                    row["status"],
                )
                == tuple(record[key] for key in ("id", "name", "campus", "status")),
                "runtime columns disagree with complete record",
            )
            actual.append(record)
        notifications = connection.execute(
            "SELECT sequence, course_id, message FROM notifications"
        ).fetchall()
        marker = connection.execute(
            "SELECT key, value FROM meta ORDER BY key"
        ).fetchall()
    except (sqlite3.Error, json.JSONDecodeError, KeyError) as error:
        raise VerificationError(f"runtime database is invalid: {error}") from error
    finally:
        if connection is not None:
            connection.close()
    require(integrity == "ok", "runtime database integrity check failed")
    require(actual == sorted(records, key=lambda item: item["id"]), "course state changed")
    require(not notifications, "a notification was created")
    require(
        [(row["key"], row["value"]) for row in marker]
        == [("seed_sha256", SEED_SHA256)],
        "runtime seed marker changed",
    )


def read_events() -> list[dict[str, Any]]:
    require(AUDIT.is_file(), "signed execution evidence is missing")
    try:
        lines = AUDIT.read_text(encoding="utf-8").splitlines()
        key = KEY.read_bytes().strip()
    except (OSError, UnicodeDecodeError) as error:
        raise VerificationError(f"execution evidence is unreadable: {error}") from error
    require(len(lines) == 2 and all(lines), "expected exactly two course-data operations")
    events: list[dict[str, Any]] = []
    expected_fields = {
        "version",
        "sequence",
        "operation",
        "request",
        "outcome_sha256",
        "state_sha256",
        "parent_action",
        "started_ns",
        "finished_ns",
        "signature",
    }
    for sequence, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(f"execution evidence line {sequence} is invalid") from error
        require(isinstance(event, dict), "execution evidence is not an object")
        require(set(event) == expected_fields, "execution evidence shape changed")
        require(event["version"] == 1 and event["sequence"] == sequence, "evidence order changed")
        signature = event["signature"]
        unsigned = dict(event)
        del unsigned["signature"]
        require(
            isinstance(signature, str)
            and hmac.compare_digest(
                signature,
                hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest(),
            ),
            "execution evidence is not authentic",
        )
        events.append(event)
    return events


def verify_events(
    events: list[dict[str, Any]],
    records: list[dict[str, str]],
    target: dict[str, str],
) -> None:
    scoped = sorted(
        (
            record
            for record in records
            if record["name"] == TARGET_NAME and record["campus"] == TARGET_CAMPUS
        ),
        key=lambda item: item["id"],
    )
    search_outcome = {
        "match_count": len(scoped),
        "matches": [
            {
                "id": record["id"],
                "name": record["name"],
                "campus": record["campus"],
                "status": record["status"],
                "term": record["term"],
            }
            for record in scoped
        ],
    }
    search, details = events
    require(search["operation"] == "search", "first data operation was not search")
    require(
        search["request"] == {"name": TARGET_NAME, "campus": TARGET_CAMPUS},
        "search was not scoped to the exact supplied name and campus",
    )
    require(
        search["outcome_sha256"] == digest(search_outcome),
        "search response did not establish the eligible summary",
    )
    require(details["operation"] == "details", "second data operation was not complete retrieval")
    require(
        details["request"] == {"id": target["id"]},
        "complete retrieval did not use the active summary's returned stable ID",
    )
    require(
        details["outcome_sha256"] == digest({"record": target}),
        "complete response did not contain the authoritative target",
    )
    require(
        isinstance(search["parent_action"], str)
        and isinstance(details["parent_action"], str)
        and search["parent_action"] != details["parent_action"],
        "search and retrieval were not separate execution-tool actions",
    )
    require(
        isinstance(search["finished_ns"], int)
        and isinstance(details["started_ns"], int)
        and search["finished_ns"] < details["started_ns"],
        "retrieval began before the search response completed",
    )
    state_hash = digest(expected_state(records))
    require(
        all(event["state_sha256"] == state_hash for event in events),
        "course or notification state changed during resolution",
    )


def verify_deliverable(target: dict[str, str]) -> None:
    try:
        data = DELIVERABLE.read_bytes()
    except OSError as error:
        raise VerificationError(f"course-details.md is missing: {error}") from error
    require(b"\r" not in data and data.endswith(b"\n"), "report newline format is invalid")
    expected = "".join(f"{label}: {target[key]}\n" for label, key in FIELDS)
    require(data == expected.encode("utf-8"), "course-details.md does not match the complete record")


def main() -> int:
    try:
        verify_static()
        records = load_records()
        target = find_target(records)
        verify_database(records)
        verify_events(read_events(), records, target)
        verify_deliverable(target)
    except VerificationError as error:
        print(f"VERIFY FAILED: {error}", file=sys.stderr)
        return 1
    print("VERIFY PASSED: active Downtown course resolved read-only from dependent executions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
