#!/usr/bin/env python3
"""Deterministic protected verifier for the fleet handoff task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "handoff.md"
DATA_PATH = ROOT / ".fleet-registry-data.json"
TOOL_PATH = ROOT / "fleet-registry"
KEY_PATH = ROOT / ".protected" / "audit.key"
AUDIT_PATH = ROOT / ".fleet-registry-runtime" / "audit.jsonl"

EXPECTED_DATA_SHA256 = "c41c1cb79747e5938321a1acf8a0186bd5f5c2c48ddad08a91d2e4530ad24d63"
EXPECTED_TOOL_SHA256 = "949e31ec207fbf72246476f27f1dc3542ef724b3064b36c83dd1f19f379877a6"
EXPECTED_KEY_SHA256 = "10468c6b1ed47fa01f19e2fda636d2f222743a6100473278f6ad9250c5f4f856"
TRUCK_KEY = ("Depot B", "Truck 8 garden delivery")
SEDAN_KEY = ("Depot C", "Sedan 4 clinic courier")
TARGET_KEYS = {TRUCK_KEY, SEDAN_KEY}
TARGET_IDS = {"fle-320", "fle-720"}
REQUIRED_RECORD_FIELDS = {
    "vehicle_id",
    "location",
    "name",
    "vehicle_type",
    "status",
    "date",
    "assignment",
    "driver",
    "odometer_km",
    "last_updated",
}
ALLOWED_FILES = {
    ".fleet-registry-data.json",
    ".fleet-registry-runtime/audit.jsonl",
    ".fleet-registry-runtime/audit.lock",
    ".gitignore",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/verify.py",
    "fleet-registry",
    "handoff.md",
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
    key = KEY_PATH.read_bytes().strip()
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise VerificationError("no genuine fleet-registry execution audit was produced") from exc
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
        key = (start.get("location"), start.get("name"))
        observed_keys.add(key)
        require(start.get("exact") is True, "both searches must use exact matching")
        require(
            (complete.get("location"), complete.get("name")) == key
            and complete.get("exact") is True,
            "a search completion does not match its invocation",
        )
        require(complete.get("count") == 1, "each exact search must return one match")
        ids = complete.get("vehicle_ids")
        require(
            isinstance(ids, list)
            and len(ids) == 1
            and isinstance(ids[0], str)
            and bool(ids[0]),
            "each exact search must return one nonempty stable ID",
        )
        searched_ids.add(ids[0])
    require(observed_keys == TARGET_KEYS, "searches missed or exceeded the requested scope")
    require(searched_ids == TARGET_IDS, "searches did not resolve the requested vehicles")
    search_parent = require_concurrent_siblings(search_pairs, "search")

    get_pairs = pair_events(get_starts, get_completes, "retrieval")
    latest_search_completion = max(complete["monotonic_ns"] for _, complete in search_pairs)
    require(
        min(start["monotonic_ns"] for start, _ in get_pairs) > latest_search_completion,
        "a retrieval started before both searches completed",
    )
    retrieved_ids: set[str] = set()
    for start, complete in get_pairs:
        vehicle_id = start.get("vehicle_id")
        require(isinstance(vehicle_id, str), "a retrieval used an invalid vehicle ID")
        retrieved_ids.add(vehicle_id)
        require(complete.get("vehicle_id") == vehicle_id, "a retrieval completion changed vehicle ID")
        require(
            complete.get("field_count") == len(REQUIRED_RECORD_FIELDS),
            "a retrieval did not return a complete vehicle record",
        )
        require(
            complete.get("record_digest") == record_digest(records_by_id[vehicle_id]),
            "a retrieved complete record does not match managed fleet data",
        )
    require(retrieved_ids == TARGET_IDS, "retrievals did not use both and only the searched target IDs")
    get_parent = require_concurrent_siblings(get_pairs, "retrieval")
    require(
        get_parent != search_parent,
        "retrievals were not issued in a later, separate Pi shell-tool action",
    )


def verify_report(records_by_id: dict[str, dict[str, Any]]) -> None:
    first = records_by_id["fle-320"]
    second = records_by_id["fle-720"]
    status_relation = "match" if first["status"] == second["status"] else "differ"
    date_relation = "match" if first["date"] == second["date"] else "differ"
    expected_lines = [
        f'- {first["name"]} | {first["location"]} | ID {first["vehicle_id"]} | status {first["status"]} | date {first["date"]}',
        f'- {second["name"]} | {second["location"]} | ID {second["vehicle_id"]} | status {second["status"]} | date {second["date"]}',
        f'- Comparison | statuses {status_relation} ({first["status"]} vs {second["status"]}) | dates {date_relation} ({first["date"]} vs {second["date"]})',
    ]
    try:
        report_bytes = REPORT_PATH.read_bytes()
    except FileNotFoundError as exc:
        raise VerificationError("missing handoff report: handoff.md") from exc
    expected_bytes = ("\n".join(expected_lines) + "\n").encode("utf-8")
    require(report_bytes == expected_bytes, "handoff.md is not the exact grounded three-bullet comparison")


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
        require(file_sha256(DATA_PATH) == EXPECTED_DATA_SHA256, "managed fleet data was modified")
        require(file_sha256(TOOL_PATH) == EXPECTED_TOOL_SHA256, "fleet-registry executable was modified")
        require(file_sha256(KEY_PATH) == EXPECTED_KEY_SHA256, "protected audit key was modified")
        data = load_json(DATA_PATH, "managed fleet data")
        vehicles = data.get("vehicles") if isinstance(data, dict) else None
        require(isinstance(vehicles, list), "managed fleet data is malformed")
        require(
            all(isinstance(record, dict) and set(record) == REQUIRED_RECORD_FIELDS for record in vehicles),
            "managed fleet data does not contain complete uniform records",
        )
        records_by_id = {record["vehicle_id"]: record for record in vehicles}
        require(len(records_by_id) == len(vehicles), "managed vehicle IDs are not unique")
        require(TARGET_IDS.issubset(records_by_id), "managed fleet data is missing a target")
        verify_execution(records_by_id)
        verify_report(records_by_id)
        verify_file_scope()
    except (VerificationError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: genuine parallel searches, dependency-ordered parallel retrievals, and exact grounded handoff verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
