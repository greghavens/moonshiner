#!/usr/bin/env python3
"""Protected deterministic verification for the meeting reconciliation task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "meetings.jsonl"
AUDIT_PATH = ROOT / "audit.log"
REPORT_PATH = ROOT / "meeting-record.md"
REQUESTED_NAME = "Quarterly Capacity Review"
REQUESTED_LOCATION = "Denver"
FORBIDDEN = {"list", "availability", "update", "cancel", "notify"}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_records() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for number, line in enumerate(DATA_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(
                f"protected data line {number} is invalid JSON: {error}"
            ) from error
        require(isinstance(record, dict), f"protected data line {number} is not an object")
        records.append({str(key): str(value) for key, value in record.items()})
    return records


def expected_record(records: list[dict[str, str]]) -> dict[str, str]:
    require(len(records) >= 50, "protected register must remain distractor-heavy")
    matches = [
        record
        for record in records
        if record.get("name") == REQUESTED_NAME
        and record.get("location") == REQUESTED_LOCATION
    ]
    require(len(matches) == 1, "protected register must have one exact requested match")
    require(matches[0].get("status") == "active", "the exact requested match must be active")
    require(
        any(
            record.get("name") == REQUESTED_NAME
            and record.get("location") != REQUESTED_LOCATION
            for record in records
        ),
        "protected register must retain same-name location distractors",
    )
    require(
        any(
            record.get("location") == REQUESTED_LOCATION
            and record.get("name") != REQUESTED_NAME
            and REQUESTED_NAME in record.get("name", "")
            for record in records
        ),
        "protected register must retain near-name Denver distractors",
    )
    require(
        any(
            record.get("id", "").startswith(matches[0]["id"][:-1])
            and record.get("id") != matches[0]["id"]
            for record in records
        ),
        "protected register must retain similar-ID distractors",
    )
    return matches[0]


def load_events() -> list[dict[str, object]]:
    require(AUDIT_PATH.is_file(), "no meeting-client invocation audit was produced")
    events: list[dict[str, object]] = []
    for number, line in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(f"audit line {number} is invalid JSON: {error}") from error
        require(isinstance(event, dict), f"audit line {number} is not an object")
        events.append(event)
    return events


def record_digest(record: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify_events(events: list[dict[str, object]], record: dict[str, str]) -> None:
    require(events, "the genuine meeting client was not used")
    operations = [event.get("operation") for event in events]
    forbidden_used = sorted({str(value) for value in operations if value in FORBIDDEN})
    require(
        not forbidden_used,
        "forbidden meeting operations used: " + ", ".join(forbidden_used),
    )
    require(
        operations == ["search", "get"],
        "required execution is exactly one exact search followed by one dependent get",
    )
    require(
        all(event.get("ok") is True for event in events),
        "both required meeting-client operations must succeed",
    )

    search_evidence = events[0].get("evidence")
    require(isinstance(search_evidence, dict), "search audit evidence is missing")
    require(
        search_evidence.get("name") == REQUESTED_NAME
        and search_evidence.get("location") == REQUESTED_LOCATION,
        "search did not use the exact requested name and location",
    )
    require(
        search_evidence.get("match_count") == 1
        and search_evidence.get("stable_ids") == [record["id"]],
        "search did not uniquely resolve the protected stable ID",
    )

    get_evidence = events[1].get("evidence")
    require(isinstance(get_evidence, dict), "get audit evidence is missing")
    require(
        get_evidence.get("stable_id") == search_evidence["stable_ids"][0],
        "get did not use the stable ID returned by the search",
    )
    require(
        get_evidence.get("found") is True
        and get_evidence.get("record_digest") == record_digest(record),
        "get did not retrieve the complete protected record",
    )


def verify_report(record: dict[str, str]) -> None:
    require(REPORT_PATH.is_file(), "meeting-record.md was not created")
    expected = "\n".join(
        [
            "| Field | Value |",
            "|---|---|",
            f"| ID | {record['id']} |",
            f"| name | {record['name']} |",
            f"| location | {record['location']} |",
            f"| status | {record['status']} |",
            f"| date | {record['date']} |",
        ]
    ) + "\n"
    actual = REPORT_PATH.read_text(encoding="utf-8")
    require(
        actual == expected,
        "meeting-record.md must be exactly the requested compact two-column table",
    )


def main() -> int:
    try:
        record = expected_record(load_records())
        verify_events(load_events(), record)
        verify_report(record)
    except (OSError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact dependent meeting lookup, read-only constraints, and table verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
