#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "appointment-audit.jsonl"
REPORT_PATH = ROOT / "appointment_audit.md"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"

EXPECTED_HASHES = {
    "appointmentctl": "9e03f333bafeba1f023fde70a840df803c0fc8cfb9a44f597969b031575887b8",
    ".appointments/records.json": "852c2b58cca2d2797dc7e6478d49bcd7a31a4dc94e1027f8d0b66771f95b9c1d",
    ".protected/audit.key": "310ffc8de8283d46118de87c40dacbfe7693a4f94b3a023110bd2a017d0b023a",
    ".protected/reference_setup.py": "4a181c430d5ada011f055e080655e816cc4a93696ce9dc77893bafce0dbb2937",
    ".gitignore": "3804bc04723783f300b3336849a63d35dd12a7693f4bfd40b1714d78ad269c00",
}
EXPECTED_SEARCHES = {
    ("Vision screening — Erin Brooks", "North Clinic"): "hea-335",
    ("Nutrition consult — Sam Adeyemi", "South Clinic"): "hea-735",
}
EXPECTED_RECORDS = {
    "hea-335": {
        "id": "hea-335",
        "name": "Vision screening — Erin Brooks",
        "location": "North Clinic",
        "date": "2026-10-26",
        "status": "rescheduled",
        "clinician": "Dr. Marisol Vega",
        "notes": "Bring current prescription eyewear.",
    },
    "hea-735": {
        "id": "hea-735",
        "name": "Nutrition consult — Sam Adeyemi",
        "location": "South Clinic",
        "date": "2026-10-27",
        "status": "confirmed",
        "clinician": "Taylor Nguyen, RD",
        "notes": "Dietary history form received.",
    },
}
EXPECTED_REPORT = (
    "| Appointment | ID | Date | Status |\n"
    "| --- | --- | --- | --- |\n"
    "| Vision screening — Erin Brooks | hea-335 | 2026-10-26 | rescheduled |\n"
    "| Nutrition consult — Sam Adeyemi | hea-735 | 2026-10-27 | confirmed |\n"
    "\n"
    "Comparison: Vision screening — Erin Brooks is rescheduled for 2026-10-26; "
    "Nutrition consult — Sam Adeyemi is confirmed for 2026-10-27.\n"
)
FORBIDDEN_OPERATIONS = {"update", "cancel", "notify"}


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
            fail(f"protected appointment input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine appointmentctl execution trace was produced")
    events = []
    key = AUDIT_KEY_PATH.read_bytes().rstrip(b"\n")
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"execution event {number} is not valid JSON")
        seal = event.pop("seal", None)
        expected = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(seal, str) or not hmac.compare_digest(seal, expected):
            fail(f"execution event {number} was not emitted intact by appointmentctl")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def sibling_tool_calls(first: dict, second: dict) -> bool:
    same_parent = (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )
    separate_sessions = (
        isinstance(first.get("process_group_id"), int)
        and isinstance(second.get("process_group_id"), int)
        and first["process_group_id"] != second["process_group_id"]
        and first.get("process_pid") == first["process_group_id"] == first.get("session_id")
        and second.get("process_pid") == second["process_group_id"] == second.get("session_id")
    )
    separate_namespaces = (
        isinstance(first.get("pid_namespace"), str)
        and isinstance(second.get("pid_namespace"), str)
        and first["pid_namespace"] != "unavailable"
        and second["pid_namespace"] != "unavailable"
        and first["pid_namespace"] != second["pid_namespace"]
    )
    return (same_parent and separate_sessions) or separate_namespaces


def tool_call_identity(event: dict) -> tuple[object, object, object, object]:
    return (
        event.get("pid_namespace"),
        event.get("parent_start_ticks"),
        event.get("session_id"),
        event.get("process_group_id"),
    )


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") in FORBIDDEN_OPERATIONS for event in events):
        fail("a forbidden appointment mutation or notification was executed")
    if len(events) != 5:
        fail("expected one help call, exactly two searches, and exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("the help call and all four required appointment operations must succeed")

    help_event, searches, gets = events[0], events[1:3], events[3:]
    if help_event.get("operation") != "help" or help_event.get("help_argument") != "--help":
        fail("the built-in help was not run before appointment-data operations")
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first appointment-data action did not contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the later appointment-data action did not contain only both retrievals")
    if min(event["start_ns"] for event in searches) <= help_event["end_ns"]:
        fail("an appointment search began before the built-in help completed")
    if tool_call_identity(help_event) in {
        tool_call_identity(event) for event in searches + gets
    }:
        fail("the help and an appointment-data operation reused a Bash tool call")

    observed = {(event.get("name"), event.get("location")): event for event in searches}
    if set(observed) != set(EXPECTED_SEARCHES):
        fail("the required exact name-and-location searches were not executed")
    for target, expected_id in EXPECTED_SEARCHES.items():
        event = observed[target]
        if event.get("result_count") != 1 or event.get("result_ids") != [expected_id]:
            fail(f"search branch did not resolve uniquely: {target[0]}")
    if not overlaps(searches[0], searches[1]) or not sibling_tool_calls(searches[0], searches[1]):
        fail("the searches were not concurrent sibling Pi Bash tool calls")

    if min(event["start_ns"] for event in gets) <= max(event["end_ns"] for event in searches):
        fail("a retrieval began before both search results were available")
    returned_ids = set(EXPECTED_SEARCHES.values())
    if {event.get("record_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the stable IDs returned by the searches")
    if not overlaps(gets[0], gets[1]) or not sibling_tool_calls(gets[0], gets[1]):
        fail("the retrievals were not concurrent sibling Pi Bash tool calls")
    if {tool_call_identity(event) for event in searches} & {
        tool_call_identity(event) for event in gets
    }:
        fail("search and retrieval stages reused a Bash tool call")

    for event in gets:
        expected = EXPECTED_RECORDS[event["record_id"]]
        if any(
            event.get(field) != expected[key]
            for field, key in (
                ("record_name", "name"),
                ("record_location", "location"),
                ("record_date", "date"),
                ("record_status", "status"),
            )
        ):
            fail(f"retrieved record did not match its search branch: {event['record_id']}")
        if event.get("record_sha256") != hashlib.sha256(canonical(expected)).hexdigest():
            fail(f"complete-record evidence is invalid: {event['record_id']}")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("appointment_audit.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("appointment_audit.md does not exactly compare the retrieved dates and statuses")


def main() -> None:
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print("PASS: genuine Pi appointment operations and grounded audit note satisfy all constraints")


if __name__ == "__main__":
    main()
