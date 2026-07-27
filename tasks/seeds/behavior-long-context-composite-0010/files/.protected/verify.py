#!/usr/bin/env python3
"""Protected verifier for the Beacon dependent-read task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "result.json"
EXECUTABLE = ROOT / "beaconctl"
SEED = ROOT / ".protected" / "catalog.json"
KEY_FILE = ROOT / ".protected" / "audit.key"
STATE = ROOT / ".beacon-runtime" / "catalog.json"
JOURNAL = ROOT / ".beacon-runtime" / "session.jsonl"
REQUESTED_NAME = "Policy Accessibility Review"
REQUESTED_LOCATION = "Beacon"
OUTPUT_FIELDS = ("id", "name", "location", "status", "date")
EXPECTED_EXECUTABLE_SHA256 = "9b002db1088bea1ccdec8b4732b10c4174b3a3091ce46f03f042e5cc499164db"
EXPECTED_SEED_SHA256 = "ba9fc6ebc11fd91e3a46cb6d270a59fa08cbcf98f7083b85329a07f0b84eefdf"
EXPECTED_KEY_SHA256 = "5cf1849b35a2eeb138bc09b855cec027800bda954bad655e4eb8763242ca7089"


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    require(path.is_file(), f"{label} is missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise VerificationError(f"{label} is invalid JSON") from error
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def check_protected_inputs() -> dict[str, Any]:
    require(
        sha256(EXECUTABLE) == EXPECTED_EXECUTABLE_SHA256,
        "the catalog executable was modified",
    )
    require(sha256(SEED) == EXPECTED_SEED_SHA256, "the catalog seed was modified")
    require(sha256(KEY_FILE) == EXPECTED_KEY_SHA256, "the audit key was modified")
    seed = load_object(SEED, "protected catalog seed")
    state = load_object(STATE, "runtime catalog state")
    require(state == seed, "catalog or notification state changed")
    return seed


def read_journal() -> list[dict[str, Any]]:
    require(JOURNAL.is_file(), "no genuine catalog execution evidence was found")
    key = KEY_FILE.read_bytes()
    entries: list[dict[str, Any]] = []
    lines = JOURNAL.read_text(encoding="utf-8").splitlines()
    require(len(lines) == 2, "the workflow must contain exactly two data operations")
    for number, line in enumerate(lines, 1):
        try:
            envelope = json.loads(line)
            entry = envelope["entry"]
            signature = envelope["signature"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise VerificationError(
                f"execution evidence line {number} is invalid"
            ) from error
        require(isinstance(entry, dict), f"evidence line {number} has no entry")
        require(isinstance(signature, str), f"evidence line {number} has no signature")
        canonical = json.dumps(
            entry,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        expected = hmac.new(key, canonical, hashlib.sha256).hexdigest()
        require(
            hmac.compare_digest(signature, expected),
            f"execution evidence line {number} is not authentic",
        )
        entries.append(entry)
    return entries


def seed_records(seed: dict[str, Any]) -> list[dict[str, Any]]:
    records = seed.get("records")
    require(isinstance(records, list), "protected catalog has no records")
    require(
        all(isinstance(record, dict) for record in records),
        "protected catalog contains an invalid record",
    )
    return records


def validate_search(
    entry: dict[str, Any],
    records: list[dict[str, Any]],
) -> str:
    require(entry.get("operation") == "search", "the first operation was not search")
    require(entry.get("exit_code") == 0, "the focused search did not succeed")
    require(
        entry.get("arguments") == {"name": REQUESTED_NAME},
        "the search was not focused on the exact requested task name",
    )
    response = entry.get("response")
    require(isinstance(response, dict), "the search returned an invalid response")
    matches = response.get("matches")
    require(isinstance(matches, list), "the search returned no candidate summaries")
    require(
        response.get("match_count") == len(matches),
        "the search match count is inconsistent",
    )
    qualifying = [
        match
        for match in matches
        if isinstance(match, dict)
        and match.get("name") == REQUESTED_NAME
        and match.get("location") == REQUESTED_LOCATION
    ]
    require(
        len(qualifying) == 1,
        "the search did not contain one unique exact-name Beacon summary",
    )
    stable_id = qualifying[0].get("id")
    require(
        isinstance(stable_id, str) and stable_id,
        "the qualifying summary has no stable ID",
    )
    expected = [
        record
        for record in records
        if record.get("name") == REQUESTED_NAME
        and record.get("location") == REQUESTED_LOCATION
    ]
    require(
        len(expected) == 1 and expected[0].get("id") == stable_id,
        "the qualifying search summary is not the controlled Beacon record",
    )
    return stable_id


def validate_get(
    entry: dict[str, Any],
    searched_id: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    require(entry.get("operation") == "get", "the second operation was not get")
    require(entry.get("exit_code") == 0, "the full-record retrieval did not succeed")
    require(
        entry.get("arguments") == {"id": searched_id},
        "the retrieval did not use only the ID returned by search",
    )
    response = entry.get("response")
    record = response.get("record") if isinstance(response, dict) else None
    require(isinstance(record, dict), "the retrieval returned no complete record")
    expected = next(
        (candidate for candidate in records if candidate.get("id") == searched_id),
        None,
    )
    require(expected is not None, "the retrieved stable ID is absent from the catalog")
    for field in OUTPUT_FIELDS:
        require(
            record.get(field) == expected.get(field),
            f"the complete record has an invalid {field} value",
        )
        require(
            isinstance(record.get(field), str),
            f"the complete record {field} is not a string",
        )
    require(
        record.get("name") == REQUESTED_NAME
        and record.get("location") == REQUESTED_LOCATION,
        "the retrieved record is not the requested Beacon task",
    )
    return record


def check_sequence(entries: list[dict[str, Any]]) -> None:
    first, second = entries
    first_end = first.get("ended_ns")
    second_start = second.get("started_ns")
    require(
        isinstance(first_end, int)
        and isinstance(second_start, int)
        and second_start >= first_end,
        "the retrieval began before the search response completed",
    )
    first_parent = first.get("parent_pid")
    second_parent = second.get("parent_pid")
    require(
        isinstance(first_parent, int)
        and isinstance(second_parent, int)
        and first_parent > 0
        and second_parent > 0
        and first_parent != second_parent,
        "search and retrieval were not separate shell-tool actions",
    )


def check_report(record: dict[str, Any]) -> None:
    report = load_object(REPORT, "result.json")
    require(
        tuple(report.keys()) == OUTPUT_FIELDS,
        "result.json fields are missing, extra, or out of order",
    )
    require(
        all(isinstance(value, str) for value in report.values()),
        "every result.json value must be a string",
    )
    expected = {field: record[field] for field in OUTPUT_FIELDS}
    require(report == expected, "result.json is not grounded in the complete record")
    canonical = json.dumps(expected, ensure_ascii=False, indent=2) + "\n"
    require(
        REPORT.read_text(encoding="utf-8") == canonical,
        "result.json does not use the exact required formatting",
    )


def verify() -> None:
    seed = check_protected_inputs()
    entries = read_journal()
    require(
        [entry.get("operation") for entry in entries] == ["search", "get"],
        "only one focused search followed by one retrieval is permitted",
    )
    require(
        all(entry.get("version") == 1 for entry in entries),
        "execution evidence version is invalid",
    )
    check_sequence(entries)
    records = seed_records(seed)
    searched_id = validate_search(entries[0], records)
    complete_record = validate_get(entries[1], searched_id, records)
    check_report(complete_record)


def main() -> int:
    try:
        verify()
    except (OSError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact Beacon search and dependent read-only retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
