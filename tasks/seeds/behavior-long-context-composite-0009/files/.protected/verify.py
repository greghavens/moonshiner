#!/usr/bin/env python3
"""Protected deterministic verification for the dependent CRM audit."""

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
SEED_PATH = PROTECTED / "accounts.json"
KEY_PATH = PROTECTED / "audit.key"
CLIENT_PATH = ROOT / "accountctl"
README_PATH = ROOT / "README.md"
HANDOFF_PATH = ROOT / "account_handoff.md"
RUNTIME = ROOT / ".crm-runtime"
DATABASE = RUNTIME / "register.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "account-audit.json"

TARGET_NAME = "Arbor Foods Cooperative"
TARGET_LOCATION = "West"
BASE_FIELDS = ("id", "name", "location", "status", "date")
ALLOWED_FIELDS = {
    "id",
    "name",
    "location",
    "status",
    "date",
    "segment",
    "renewalQuarter",
    "accountManager",
    "notes",
}
OUTPUT_FIELDS = (
    "id",
    "name",
    "location",
    "status",
    "date",
    "accountManager",
)
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

SEED_SHA256 = "ca337802c04bc2cb4a27c0c6b1b77b933972d9f4bec1cf6591bb01b925e9fd80"
KEY_SHA256 = "1e9206ca6a914e5816a236b4a3ba6eae14444e469c1c1387a6bb409d9c45626b"
CLIENT_SHA256 = "a3dfcc5d1447155b21d3600342b3d77eed746dc080f0b18f5905fc1e685f5acf"
README_SHA256 = "644a11a9439794fadd5665a82e8c5ad07dfb72c902e5b6cd79fe5750cf5dba04"
HANDOFF_SHA256 = "72fd6fd99a0ff1e50498d98f933ac085ff40ab156c9fa95c48bbbbf896f94a8e"


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
    require(file_sha256(SEED_PATH) == SEED_SHA256, "protected CRM seed changed")
    require(file_sha256(KEY_PATH) == KEY_SHA256, "protected evidence key changed")
    require(file_sha256(CLIENT_PATH) == CLIENT_SHA256, "account executable changed")
    require(file_sha256(README_PATH) == README_SHA256, "README changed")
    require(file_sha256(HANDOFF_PATH) == HANDOFF_SHA256, "handoff context changed")


def load_seed() -> list[dict[str, str]]:
    try:
        payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"protected CRM seed is unreadable: {error}") from error
    require(isinstance(payload, dict), "protected CRM seed is not an object")
    require(payload.get("schema_version") == 1, "protected CRM seed schema changed")
    records = payload.get("records")
    require(isinstance(records, list) and bool(records), "protected CRM seed is empty")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in records:
        require(isinstance(item, dict), "protected account record is not an object")
        require(
            set(BASE_FIELDS).issubset(item) and set(item).issubset(ALLOWED_FIELDS),
            "protected account record shape changed",
        )
        require(
            all(isinstance(value, str) for value in item.values()),
            "protected account record contains a non-string value",
        )
        record = dict(item)
        require(record["id"] not in seen, "protected CRM seed has duplicate stable IDs")
        seen.add(record["id"])
        normalized.append(record)
    return normalized


def hamming_distance(left: str, right: str) -> int | None:
    if len(left) != len(right):
        return None
    return sum(a != b for a, b in zip(left, right))


def find_target(records: list[dict[str, str]]) -> dict[str, str]:
    matches = [
        record
        for record in records
        if record["name"] == TARGET_NAME
        and record["location"] == TARGET_LOCATION
    ]
    require(len(matches) == 1, "protected target does not resolve uniquely")
    target = matches[0]
    require(target["status"] == "active", "protected target is not active")
    require(
        any(
            record["name"] == TARGET_NAME
            and record["location"] != TARGET_LOCATION
            and record["status"] in {"pending", "archived", "active"}
            for record in records
        ),
        "same-name location distractors are missing",
    )
    require(
        any(
            record["location"] == TARGET_LOCATION
            and record["name"] != TARGET_NAME
            and "Arbor Food" in record["name"]
            for record in records
        ),
        "near-name West distractors are missing",
    )
    require(
        any(
            record["id"] != target["id"]
            and hamming_distance(record["id"], target["id"]) == 1
            for record in records
        ),
        "similar stable-ID distractors are missing",
    )
    require(
        "accountManager" not in target,
        "the protected target must exercise absent-field handling",
    )
    return target


def expected_state(records: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "records": sorted(records, key=lambda record: record["id"]),
        "notifications": [],
    }


def verify_runtime_shape() -> None:
    require(RUNTIME.is_dir(), "CRM runtime is missing")
    entries = {path.name for path in RUNTIME.iterdir()}
    require(
        entries == {"register.sqlite3", "audit.jsonl", ".lock"},
        "CRM runtime contains missing or unexpected artifacts",
    )


def verify_database(records: list[dict[str, str]]) -> None:
    require(DATABASE.is_file(), "CRM runtime database is missing")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        rows = connection.execute(
            """
            SELECT id, name, location, status, date, detail_json
            FROM accounts
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
                "runtime account columns disagree with full detail",
            )
            actual_records.append(detail)
        notifications = connection.execute(
            """
            SELECT sequence, account_id, recipient, message
            FROM notifications
            ORDER BY sequence
            """
        ).fetchall()
        marker_rows = connection.execute(
            "SELECT key, value FROM register_meta ORDER BY key"
        ).fetchall()
    except (sqlite3.Error, json.JSONDecodeError, KeyError) as error:
        raise VerificationError(f"CRM runtime database is invalid: {error}") from error
    finally:
        if connection is not None:
            connection.close()

    require(integrity == "ok", "CRM runtime database failed integrity check")
    require(
        actual_records == sorted(records, key=lambda record: record["id"]),
        "account records changed or runtime state is not authoritative",
    )
    require(not notifications, "a CRM notification was sent")
    require(
        [(row["key"], row["value"]) for row in marker_rows]
        == [("seed_sha256", SEED_SHA256)],
        "CRM seed marker changed",
    )


def read_events() -> list[dict[str, Any]]:
    require(AUDIT.is_file(), "signed CRM execution evidence is missing")
    try:
        lines = AUDIT.read_text(encoding="utf-8").splitlines()
        key = KEY_PATH.read_bytes().strip()
    except (OSError, UnicodeDecodeError) as error:
        raise VerificationError(f"cannot read signed execution evidence: {error}") from error
    require(len(lines) == 2 and all(lines), "expected exactly two CRM data operations")

    events: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(
                f"execution evidence line {index} is invalid"
            ) from error
        require(isinstance(event, dict), f"evidence line {index} is not an object")
        require(set(event) == EVENT_FIELDS, f"evidence line {index} shape changed")
        require(event["version"] == 1, f"evidence line {index} version changed")
        require(event["sequence"] == index, "CRM operation sequence is invalid")
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


def verify_events(
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
                "status": target["status"],
            }
        ],
    }
    get_result = {"record": target}
    state_sha256 = digest(expected_state(records))

    search, get = events
    require(search["action"] == "search", "first CRM operation was not search")
    require(
        search["request"]
        == {"name": TARGET_NAME, "location": TARGET_LOCATION},
        "search was not jointly constrained to the exact name and location",
    )
    require(
        search["success"] is True
        and search["violation"] is False
        and search["error"] is None,
        "exact search failed or violated audit scope",
    )
    require(
        search["result_count"] == 1
        and search["sole_id"] == target["id"]
        and search["result_sha256"] == digest(search_result),
        "search did not return exactly the unique active account summary",
    )

    require(get["action"] == "get", "second CRM operation was not full retrieval")
    require(
        get["request"] == {"id": search["sole_id"]},
        "full retrieval did not use the stable ID returned by search",
    )
    require(
        get["success"] is True
        and get["violation"] is False
        and get["error"] is None,
        "full-detail retrieval failed or violated audit scope",
    )
    require(
        get["result_count"] == 1
        and get["sole_id"] == target["id"]
        and get["result_sha256"] == digest(get_result),
        "retrieval did not return the authoritative full account record",
    )
    require(
        isinstance(search["action_id"], str)
        and isinstance(get["action_id"], str)
        and search["action_id"] != get["action_id"],
        "search and retrieval were not separate shell-tool actions",
    )
    require(
        isinstance(search["finished_ns"], int)
        and isinstance(get["started_ns"], int)
        and search["finished_ns"] < get["started_ns"],
        "full retrieval began before the search result was available",
    )

    for event in events:
        require(event["seed_sha256"] == SEED_SHA256, "evidence seed digest changed")
        require(event["state_sha256"] == state_sha256, "CRM state changed")


def verify_deliverable(target: dict[str, str]) -> None:
    require(DELIVERABLE.is_file(), "account-audit.json is missing")
    try:
        text = DELIVERABLE.read_text(encoding="utf-8")
        actual = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"account-audit.json is unreadable: {error}") from error
    require(isinstance(actual, dict), "account-audit.json is not an object")
    require(
        list(actual) == list(OUTPUT_FIELDS),
        "account-audit.json keys are missing, extra, or out of order",
    )
    expected = {
        field: target.get(field, "unknown")
        for field in OUTPUT_FIELDS
    }
    expected_text = json.dumps(expected, ensure_ascii=False, indent=2) + "\n"
    require(
        actual == expected and text == expected_text,
        "account-audit.json is not grounded in full detail or has wrong formatting",
    )


def verify_workspace_shape() -> None:
    allowed = {
        ".agents",
        ".codex",
        ".git",
        ".gitignore",
        ".protected",
        ".crm-runtime",
        ".sandbox-home",
        "README.md",
        "account_handoff.md",
        "accountctl",
        "account-audit.json",
        "reference_driver.py",
        "__pycache__",
    }
    unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    require(not unexpected, f"unexpected scratch artifacts remain: {unexpected}")
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
        events = read_events()
        verify_events(events, records, target)
        verify_deliverable(target)
        verify_workspace_shape()
    except VerificationError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print(
        "verification passed: exact search, dependent full detail, read-only state, "
        "and absent-field reporting"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
