#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "trip-audit.jsonl"
REPLY_PATH = ROOT / "audit_reply.txt"
DATA_PATH = ROOT / ".trips" / "records.json"
AUDIT_KEY = b"moonshiner-pi-travel-audit-0082-v1"

EXPECTED_HASHES = {
    "trip-registry": "e7bc357900e83d5af0ee9cc9e67088d6db91582cc7d3350bf0560bcb12602749",
    ".trips/records.json": "ecbf47d2d0143ba0516da7889ca7d57a86d69b53ffe5ef6ed65264a879a0d13e",
    "README.md": "cbb5774b3b30d857bacd2a8f6c666ad2be4ab83939f039f4f3297b7a97f07a22",
    ".gitignore": "82243e49284b76510e0db9203cd8748f65cbbdbbef9f981ea2b809bf18c7fef2",
}
EXPECTED_SEARCHES = {
    ("Seoul Trade Delegation", "Seoul"),
    ("Taipei Standards Forum", "Taipei"),
}
FORBIDDEN_OPERATIONS = {
    "list",
    "preferences",
    "availability",
    "create",
    "update",
    "cancel",
    "notify",
}
TARGET_IDS = ("tra-182", "tra-582")
TARGET_STATUSES = ("approved", "waitlisted")
UNRELATED_MARKERS = (
    "tra-982",
    "tra-882",
    "Seoul Layover",
    "Taipei Transit Desk",
    "cancelled",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected trip-registry input changed: {relative}")


def load_records() -> list[dict]:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if payload.get("notifications") != [] or payload.get("mutation_log") != []:
        fail("trip registry was mutated or a notification was sent")
    trips = payload.get("trips")
    if not isinstance(trips, list):
        fail("protected trip registry is malformed")
    return trips


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine trip-registry execution trace was produced")
    events = []
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {number} was not emitted intact by the registry")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict, second: dict) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def separate_tool_calls(first: dict, second: dict) -> bool:
    return (
        isinstance(first.get("process_group_id"), int)
        and isinstance(second.get("process_group_id"), int)
        and first["process_group_id"] != second["process_group_id"]
        and first.get("process_pid") == first["process_group_id"]
        and second.get("process_pid") == second["process_group_id"]
        and first.get("session_id") == first["process_pid"]
        and second.get("session_id") == second["process_pid"]
    )


def expected_targets(records: list[dict]) -> dict[tuple[str, str], dict]:
    targets: dict[tuple[str, str], dict] = {}
    for name, location in EXPECTED_SEARCHES:
        matches = [
            record
            for record in records
            if record.get("name") == name and record.get("location") == location
        ]
        if len(matches) != 1:
            fail("protected reference state does not have two unique target branches")
        targets[(name, location)] = matches[0]
    return targets


def verify_execution(events: list[dict], records: list[dict]) -> None:
    if any(event.get("operation") in FORBIDDEN_OPERATIONS for event in events):
        fail("a forbidden trip-registry operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("all required trip-registry operations must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry-execution action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry-execution action must contain only both gets")

    observed_searches = {
        (event.get("name"), event.get("location")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-location searches were not executed")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both trip branches must resolve to exactly one stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        or not event["result_ids"][0]
        for event in searches
    ):
        fail("a search did not return one auditable stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not two concurrent sibling Pi Bash-tool calls")

    targets = expected_targets(records)
    returned_ids = set()
    for event in searches:
        key = (event["name"], event["location"])
        target = targets[key]
        expected_summary = {
            "matches": [
                {
                    "name": target["name"],
                    "stable_id": target["stable_id"],
                    "location": target["location"],
                }
            ]
        }
        if event.get("result_ids") != [target["stable_id"]]:
            fail("a search stable ID does not belong to its own branch")
        if event.get("result_digest") != file_sha256_bytes(canonical(expected_summary)):
            fail("search evidence does not match the genuine registry result")
        returned_ids.add(target["stable_id"])

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a get began before both search results were available")
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("gets did not use exactly the stable IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete trip record was not retrieved")
    records_by_id = {record["stable_id"]: record for record in records}
    for event in gets:
        expected_output = {"record": records_by_id[event["stable_id"]]}
        if event.get("result_digest") != file_sha256_bytes(canonical(expected_output)):
            fail("get evidence does not match the complete registry record")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the gets were not two concurrent sibling Pi Bash-tool calls")
    search_groups = {event["process_group_id"] for event in searches}
    get_groups = {event["process_group_id"] for event in gets}
    if search_groups & get_groups:
        fail("search and get stages reused a Bash-tool call")


def file_sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_reply() -> None:
    if not REPLY_PATH.is_file():
        fail("audit_reply.txt is missing")
    text = REPLY_PATH.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        fail("audit_reply.txt must be newline-terminated")
    reply = text.rstrip("\n")
    words = re.findall(r"\b[\w'-]+\b", reply, flags=re.UNICODE)
    if not words or len(words) >= 75:
        fail("the reply must contain fewer than 75 words")
    if not reply.endswith("No changes made."):
        fail('the reply must end with the exact sentence "No changes made."')
    positions = {
        value: [
            match.start()
            for match in re.finditer(
                rf"(?<![\w-]){re.escape(value)}(?![\w-])", reply
            )
        ]
        for value in (*TARGET_IDS, *TARGET_STATUSES)
    }
    if any(not positions[stable_id] for stable_id in TARGET_IDS):
        fail("the reply must include both retrieved stable IDs")
    if any(not positions[status] for status in TARGET_STATUSES):
        fail("the reply must include both retrieved status values")
    if re.search(r"\brespectively\b", reply, re.I):
        id_order = sorted(range(2), key=lambda index: positions[TARGET_IDS[index]][0])
        status_order = sorted(
            range(2), key=lambda index: positions[TARGET_STATUSES[index]][0]
        )
        mappings_are_clear = id_order == status_order
    else:
        mappings_are_clear = all(
            min(
                abs(id_position - status_position)
                for id_position in positions[TARGET_IDS[index]]
                for status_position in positions[TARGET_STATUSES[index]]
            )
            < min(
                abs(id_position - status_position)
                for id_position in positions[TARGET_IDS[index]]
                for status_position in positions[TARGET_STATUSES[1 - index]]
            )
            for index in range(2)
        )
    if not mappings_are_clear:
        fail("each retrieved status must be associated with its own stable ID")
    folded_reply = reply.casefold()
    if any(marker.casefold() in folded_reply for marker in UNRELATED_MARKERS):
        fail("the reply discloses a stale or unrelated match")


def main() -> None:
    verify_protected_inputs()
    records = load_records()
    verify_execution(load_events(), records)
    verify_reply()
    print("PASS: short reply and genuine Pi trip-registry execution satisfy all constraints")


if __name__ == "__main__":
    main()
