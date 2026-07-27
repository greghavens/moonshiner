#!/usr/bin/env python3
"""Deterministic protected verifier for the meeting operations check."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "meeting_comparison.md"
DATA_PATH = ROOT / ".meeting-registry-data.json"
TOOL_PATH = ROOT / "meeting-registry"
KEY_PATH = ROOT / ".protected" / "audit.key"
AUDIT_PATH = ROOT / ".meeting-registry-runtime" / "audit.jsonl"

EXPECTED_DATA_SHA256 = "84ddb8e3e92e8c3c770916a49d5992c996cb6ea7c771e58c33cd5cc83d4453b4"
EXPECTED_TOOL_SHA256 = "3ce4b437fda92e5c77449fd759f684356000c245fc1c31350f2b81cef4e5cfdf"
EXPECTED_KEY_SHA256 = "23c9976ab7c050967a116134e47aac7ffa21d3667eba1c606705df1014cd2bf6"
MUSEUM_KEY = ("Seattle", "Museum Partnership Briefing")
CAMPAIGN_KEY = ("Portland", "Fall Campaign Retrospective")
TARGET_KEYS = {MUSEUM_KEY, CAMPAIGN_KEY}
TARGET_IDS = {"mtg-sea-418", "mtg-pdx-274"}
REQUIRED_RECORD_FIELDS = {
    "meeting_id",
    "city",
    "name",
    "status",
    "date",
    "venue",
    "organizer",
    "attendees",
    "agenda",
    "last_updated",
}
ALLOWED_FILES = {
    ".gitignore",
    ".meeting-registry-data.json",
    ".meeting-registry-runtime/audit.jsonl",
    ".meeting-registry-runtime/audit.lock",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/verify.py",
    "meeting-registry",
    "meeting_comparison.md",
}


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError as exc:
        raise VerificationError(f"missing managed file: {path.relative_to(ROOT)}") from exc


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing {label}: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"{label} is invalid JSON: {exc}") from exc


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()


def load_events() -> list[dict[str, Any]]:
    try:
        key = KEY_PATH.read_bytes().strip()
    except FileNotFoundError as exc:
        raise VerificationError("missing protected audit key") from exc
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise VerificationError("no genuine meeting-registry execution audit was produced") from exc
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VerificationError(f"audit line {line_number} is invalid JSON") from exc
        require(isinstance(envelope, dict), f"audit line {line_number} is not an object")
        require(set(envelope) == {"payload", "mac"}, f"audit line {line_number} has invalid fields")
        payload = envelope.get("payload")
        mac = envelope.get("mac")
        require(
            isinstance(payload, dict) and isinstance(mac, str),
            f"audit line {line_number} is malformed",
        )
        expected_mac = hmac.new(key, canonical(payload).encode("utf-8"), hashlib.sha256).hexdigest()
        require(hmac.compare_digest(mac, expected_mac), f"audit line {line_number} is not authentic")
        events.append(payload)
    return events


def pair_events(
    starts: list[dict[str, Any]], completes: list[dict[str, Any]], label: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    complete_by_token = {event.get("token"): event for event in completes}
    require(len(complete_by_token) == len(completes), f"duplicate {label} completion token")
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for start in starts:
        token = start.get("token")
        require(isinstance(token, str) and token in complete_by_token, f"{label} did not complete")
        complete = complete_by_token[token]
        require(
            isinstance(start.get("monotonic_ns"), int)
            and isinstance(complete.get("monotonic_ns"), int)
            and complete["monotonic_ns"] > start["monotonic_ns"],
            f"{label} completion ordering is invalid",
        )
        pairs.append((start, complete))
    return pairs


def require_concurrent_siblings(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]], label: str
) -> tuple[int, int]:
    require(len(pairs) == 2, f"expected exactly two {label} operations")
    first_start, first_complete = pairs[0]
    second_start, second_complete = pairs[1]
    require(first_start.get("pid") != second_start.get("pid"), f"{label}s were not separate processes")
    parent_pid = first_start.get("parent_pid")
    parent_ticks = first_start.get("parent_start_ticks")
    require(
        isinstance(parent_pid, int)
        and isinstance(parent_ticks, int)
        and parent_pid == second_start.get("parent_pid")
        and parent_ticks == second_start.get("parent_start_ticks"),
        f"{label}s were not launched by the same Pi shell-tool action",
    )
    require(
        first_start["monotonic_ns"] < second_complete["monotonic_ns"]
        and second_start["monotonic_ns"] < first_complete["monotonic_ns"],
        f"the two {label} processes did not overlap",
    )
    return parent_pid, parent_ticks


def verify_execution(records_by_id: dict[str, dict[str, Any]]) -> None:
    events = load_events()
    allowed_events = {"search_started", "search_completed", "get_started", "get_completed"}
    unexpected = [event.get("event") for event in events if event.get("event") not in allowed_events]
    require(not unexpected, f"forbidden, rejected, or invalid operations were attempted: {unexpected}")
    require(len(events) == 8, "workflow must contain only two searches and two retrievals")

    search_starts = [event for event in events if event.get("event") == "search_started"]
    search_completes = [event for event in events if event.get("event") == "search_completed"]
    get_starts = [event for event in events if event.get("event") == "get_started"]
    get_completes = [event for event in events if event.get("event") == "get_completed"]
    require(len(search_starts) == 2 and len(search_completes) == 2, "expected two completed searches")
    require(len(get_starts) == 2 and len(get_completes) == 2, "expected two completed retrievals")

    search_pairs = pair_events(search_starts, search_completes, "search")
    observed_keys: set[tuple[str, str]] = set()
    searched_ids: set[str] = set()
    for start, complete in search_pairs:
        key = (start.get("city"), start.get("name"))
        observed_keys.add(key)
        require(start.get("exact") is True, "both searches must use exact matching")
        require(
            (complete.get("city"), complete.get("name")) == key
            and complete.get("exact") is True,
            "a search completion does not match its invocation",
        )
        require(complete.get("count") == 1, "each exact search must return one match")
        ids = complete.get("meeting_ids")
        require(
            isinstance(ids, list)
            and len(ids) == 1
            and isinstance(ids[0], str)
            and bool(ids[0]),
            "each exact search must return one nonempty stable ID",
        )
        searched_ids.add(ids[0])
    require(observed_keys == TARGET_KEYS, "searches missed or exceeded the requested scope")
    require(searched_ids == TARGET_IDS, "searches did not resolve the requested meetings")
    search_parent = require_concurrent_siblings(search_pairs, "search")

    get_pairs = pair_events(get_starts, get_completes, "retrieval")
    latest_search_completion = max(complete["monotonic_ns"] for _, complete in search_pairs)
    require(
        min(start["monotonic_ns"] for start, _ in get_pairs) > latest_search_completion,
        "a retrieval started before both searches completed",
    )
    retrieved_ids: set[str] = set()
    for start, complete in get_pairs:
        meeting_id = start.get("meeting_id")
        require(
            isinstance(meeting_id, str) and meeting_id in records_by_id,
            "a retrieval used an invalid meeting ID",
        )
        retrieved_ids.add(meeting_id)
        require(complete.get("meeting_id") == meeting_id, "a retrieval completion changed meeting ID")
        require(
            complete.get("field_count") == len(REQUIRED_RECORD_FIELDS),
            "a retrieval did not return a complete meeting record",
        )
        require(
            complete.get("record_digest") == record_digest(records_by_id[meeting_id]),
            "a retrieved complete record does not match managed meeting data",
        )
    require(retrieved_ids == TARGET_IDS, "retrievals did not use both and only the searched target IDs")
    get_parent = require_concurrent_siblings(get_pairs, "retrieval")
    require(
        get_parent != search_parent,
        "retrievals were not issued in a later, separate Pi shell-tool action",
    )


def verify_report(records_by_id: dict[str, dict[str, Any]]) -> None:
    ordered = sorted(
        (records_by_id[meeting_id] for meeting_id in TARGET_IDS),
        key=lambda record: record["name"],
    )
    first, second = ordered
    status_relation = "match" if first["status"] == second["status"] else "differ"
    date_relation = "match" if first["date"] == second["date"] else "differ"
    expected_lines = [
        f'- {first["name"]} | {first["city"]} | ID {first["meeting_id"]} | status {first["status"]} | date {first["date"]}',
        f'- {second["name"]} | {second["city"]} | ID {second["meeting_id"]} | status {second["status"]} | date {second["date"]}',
        f'- Comparison | statuses {status_relation} ({first["status"]} vs {second["status"]}) | dates {date_relation} ({first["date"]} vs {second["date"]})',
    ]
    try:
        report_bytes = REPORT_PATH.read_bytes()
    except FileNotFoundError as exc:
        raise VerificationError("missing meeting comparison: meeting_comparison.md") from exc
    expected_bytes = ("\n".join(expected_lines) + "\n").encode("utf-8")
    require(
        report_bytes == expected_bytes,
        "meeting_comparison.md is not the exact grounded alphabetical comparison",
    )


def verify_file_scope() -> None:
    observed = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(ROOT).parts
    }
    extras = sorted(observed - ALLOWED_FILES)
    require(not extras, f"unexpected files were created in the workspace: {extras}")


def main() -> int:
    try:
        require(file_sha256(DATA_PATH) == EXPECTED_DATA_SHA256, "managed meeting data was modified")
        require(file_sha256(TOOL_PATH) == EXPECTED_TOOL_SHA256, "meeting-registry executable was modified")
        require(file_sha256(KEY_PATH) == EXPECTED_KEY_SHA256, "protected audit key was modified")
        data = load_json(DATA_PATH, "managed meeting data")
        meetings = data.get("meetings") if isinstance(data, dict) else None
        require(isinstance(meetings, list), "managed meeting data is malformed")
        require(
            all(isinstance(record, dict) and set(record) == REQUIRED_RECORD_FIELDS for record in meetings),
            "managed meeting data does not contain complete uniform records",
        )
        records_by_id = {record["meeting_id"]: record for record in meetings}
        require(len(records_by_id) == len(meetings), "managed meeting IDs are not unique")
        require(TARGET_IDS.issubset(records_by_id), "managed meeting data is missing a target")
        verify_execution(records_by_id)
        verify_report(records_by_id)
        verify_file_scope()
    except (VerificationError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: genuine parallel searches, dependency-ordered parallel retrievals, and exact alphabetical comparison verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
