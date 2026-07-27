#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi subscription workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "subscription-audit.jsonl"
REPORT_PATH = ROOT / "subscription_check.md"
DATA_PATH = ROOT / ".subscriptions" / "records.json"
AUDIT_KEY = b"moonshiner-pi-subscription-audit-0218-v1"
REFERENCE_MARKER_DIGEST = (
    "0c7bd2da1c745dcb5f851c73fecbe646336048410670e7ab411061de13d4eccd"
)

EXPECTED_HASHES = {
    "subscription-registry": "df42d04dac2b8b09f5cb88b6297753c49792e39ce92dc9b4898516fdcefe1457",
    ".subscriptions/records.json": "40ac01fa9100e65da21e5372b4aba18973a1b51f5f9188fa6aee48032100e0a9",
    "README.md": "69a3b40da50c429d15d992ffcdf6fc9eb5fdd30d5eedb30269628565efcc1739",
    ".gitignore": "49d4128b4a0b9330de9431a852223e8c969c7a61e6d81729cab2cd1d89717127",
    ".harness/reference_setup.py": "0cf0efd2e3c5ada0b53902483b4c90a9a8e5b123a887a37ce0da13d8ec9b2494",
}
EXPECTED_SEARCH_IDS = {
    ("Clinic backup line", "Health Account"): "sub-482",
    ("Museum guest Wi-Fi plan", "Arts Account"): "sub-913",
}
EXPECTED_IDS = set(EXPECTED_SEARCH_IDS.values())
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_WORKSPACE_FILES = set(EXPECTED_HASHES) | {
    ".harness/runtime/subscription-audit.jsonl",
    ".harness/verify.py",
    "subscription_check.md",
}
ALLOWED_WORKSPACE_DIRECTORIES = {
    ".harness",
    ".harness/runtime",
    ".subscriptions",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected subscription input changed: {relative}")


def verify_no_scratch_artifacts() -> None:
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] == ".git":
            continue
        rendered = relative.as_posix()
        if rendered == ".reference_solution":
            if not path.is_file() or file_sha256(path) != REFERENCE_MARKER_DIGEST:
                fail("reference validation marker is invalid")
            continue
        if path.is_dir():
            if rendered not in ALLOWED_WORKSPACE_DIRECTORIES:
                fail(f"unexpected scratch directory exists: {rendered}")
        elif rendered not in ALLOWED_WORKSPACE_FILES:
            fail(f"unexpected scratch file exists: {rendered}")


def load_records() -> dict[str, dict]:
    try:
        payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        records = payload["subscriptions"]
    except (OSError, KeyError, json.JSONDecodeError):
        fail("protected subscription data is malformed")
    if not isinstance(records, list):
        fail("protected subscription data has no record list")
    by_id = {
        record.get("id"): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    }
    if not EXPECTED_IDS.issubset(by_id):
        fail("protected subscription data is missing a target record")
    return by_id


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine subscription-registry execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"audit event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by the registry")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict, second: dict) -> bool:
    return (
        first["parent_pid"] == second["parent_pid"]
        and first["parent_start_ticks"] == second["parent_start_ticks"]
        and first["parent_start_ticks"] != "unavailable"
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


def verify_execution(events: list[dict], records_by_id: dict[str, dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an operation outside the read-only subscription check was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("all required subscription-registry operations must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the later registry action must contain only both gets")

    observed_searches = {
        (event.get("query"), event.get("account")) for event in searches
    }
    if observed_searches != set(EXPECTED_SEARCH_IDS):
        fail("the two required exact query-and-account searches were not executed")
    for event in searches:
        key = (event.get("query"), event.get("account"))
        if event.get("result_count") != 1:
            fail("both subscription searches must resolve to exactly one stable ID")
        if event.get("result_ids") != [EXPECTED_SEARCH_IDS[key]]:
            fail("a search did not return its one auditable stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not concurrent sibling Pi Bash tool calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a get began before both search results were available")
    if {event.get("stable_id") for event in gets} != EXPECTED_IDS:
        fail("gets did not use exactly the stable IDs returned by the searches")
    for event in gets:
        stable_id = event.get("stable_id")
        record = records_by_id[stable_id]
        if event.get("result_count") != 1:
            fail("a complete subscription record was not retrieved")
        if event.get("field_count") != len(record):
            fail("a retrieval did not return the complete subscription record")
        if event.get("record_digest") != hashlib.sha256(canonical(record)).hexdigest():
            fail("a retrieval did not return the protected subscription record intact")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the gets were not concurrent sibling Pi Bash tool calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in gets
    }:
        fail("search and get stages reused a Bash tool call")


def expected_report(records_by_id: dict[str, dict]) -> str:
    clinic = records_by_id[EXPECTED_SEARCH_IDS[("Clinic backup line", "Health Account")]]
    museum = records_by_id[
        EXPECTED_SEARCH_IDS[("Museum guest Wi-Fi plan", "Arts Account")]
    ]
    status_word = "match" if clinic["status"] == museum["status"] else "differ"
    date_word = "match" if clinic["date"] == museum["date"] else "differ"
    return (
        f"{clinic['name']} in {clinic['account']} has status {clinic['status']} "
        f"and date {clinic['date']}.\n"
        f"{museum['name']} in {museum['account']} has status {museum['status']} "
        f"and date {museum['date']}.\n"
        f"The returned statuses {status_word}, and the returned dates {date_word}.\n"
    )


def verify_report(records_by_id: dict[str, dict]) -> None:
    if not REPORT_PATH.is_file():
        fail("subscription_check.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected_report(records_by_id):
        fail("subscription_check.md does not have the exact grounded comparison")


def main() -> None:
    verify_protected_inputs()
    verify_no_scratch_artifacts()
    records_by_id = load_records()
    verify_execution(load_events(), records_by_id)
    verify_report(records_by_id)
    print(
        "PASS: grounded subscription comparison and genuine Pi registry execution "
        "satisfy all constraints"
    )


if __name__ == "__main__":
    main()
