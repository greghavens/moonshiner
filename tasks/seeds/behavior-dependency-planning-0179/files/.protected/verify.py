#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0179."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".civic"
INITIAL_APPLICATIONS_PATH = ROOT / ".protected" / "initial_applications.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
INITIAL_RECEIPTS_PATH = ROOT / ".protected" / "initial_receipts.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "civicctl"
SENIOR_ID = "application-2179"
RAIN_ID = "application-6179"
REASON = "Applicant withdrew the duplicate application"
EXPECTED_EXECUTABLE_SHA256 = "f3ac4cad919889d02eaa2ce4e6dd455f6f437253d95e0dc721eebb565f4dfc03"
EXPECTED_INITIAL_APPLICATIONS_SHA256 = "f17837ed20504d327cef78100dddd63819d652ba880d37c4c7822581a657617b"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "45490647620cb53879f5722db4c6c38f361b04df69d30dc93c9a71e1b9cdf62d"
EXPECTED_INITIAL_RECEIPTS_SHA256 = "5612bdd13a07611fc73bb6c9d1f3ded35cefe8b70ac608b361c53627687e4fc0"
EXPECTED_AUDIT_KEY_SHA256 = "fb608cfc8eea22e361caf0f57acff73df74e9d216dd38e7d069fef7241eb5743"
EXPECTED_FILES = {
    ".civic/applications.json",
    ".civic/audit.jsonl",
    ".civic/lock",
    ".civic/notifications.json",
    ".civic/receipts.json",
    ".protected/audit.key",
    ".protected/initial_applications.json",
    ".protected/initial_notifications.json",
    ".protected/initial_receipts.json",
    ".protected/verify.py",
    "civicctl",
}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path.relative_to(ROOT)}: {error}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} is not a JSON object")
    return value


def application_map(
    document: dict[str, Any], label: str
) -> dict[str, dict[str, Any]]:
    applications = document.get("applications")
    if document.get("version") != 1 or not isinstance(applications, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(application, dict)
        and isinstance(application.get("id"), str)
        and bool(application["id"])
        for application in applications
    ):
        fail(f"{label} contains an invalid application")
    result = {application["id"]: application for application in applications}
    if len(result) != len(applications):
        fail(f"{label} contains duplicate IDs")
    return result


def require_file_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected {label}: {error}")
    if actual != expected:
        fail(f"protected {label} was modified")


def verify_workspace_files() -> None:
    actual = {
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(ROOT).parts
        and "__pycache__" not in path.relative_to(ROOT).parts
    }
    extras = sorted(actual - EXPECTED_FILES)
    missing = sorted(EXPECTED_FILES - actual)
    if extras:
        fail("unexpected workspace files remain: " + ", ".join(extras))
    if missing:
        fail("required sandbox files are missing: " + ", ".join(missing))


def load_audit() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with (STATE_DIR / "audit.jsonl").open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    fail(
                        f"operation history line {line_number} is invalid JSON: {error}"
                    )
                if not isinstance(value, dict):
                    fail(f"operation history line {line_number} is not an object")
                events.append(value)
    except OSError as error:
        fail(f"cannot read operation history: {error}")
    return events


def validate_seals(events: list[dict[str, Any]]) -> None:
    try:
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    if not key:
        fail("protected audit key is empty")
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        if not isinstance(seal, str):
            fail(f"operation event {index} has no seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, expected):
            fail(f"operation event {index} has an invalid seal")


def interval(event: dict[str, Any], label: str) -> tuple[int, int]:
    started = event.get("started_ns")
    finished = event.get("finished_ns")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(finished, int)
        or isinstance(finished, bool)
        or started >= finished
    ):
        fail(f"{label} has an invalid execution interval")
    return started, finished


def intervals_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def require_sibling_calls(
    first_event: dict[str, Any],
    second_event: dict[str, Any],
    first_interval: tuple[int, int],
    second_interval: tuple[int, int],
    label: str,
) -> None:
    for index, event in enumerate((first_event, second_event), 1):
        for field in ("process_pid", "process_group_id", "session_id", "parent_pid"):
            value = event.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                fail(f"{label} call {index} has invalid process evidence")
        for field in ("process_start_ticks", "parent_start_ticks"):
            value = event.get(field)
            if not isinstance(value, str) or not value or value == "unavailable":
                fail(f"{label} call {index} lacks stable process evidence")
    same_parent = (
        first_event["parent_pid"] == second_event["parent_pid"]
        and first_event["parent_start_ticks"] == second_event["parent_start_ticks"]
    )
    separate_process_groups = (
        first_event["process_group_id"] != second_event["process_group_id"]
        and first_event["process_pid"] == first_event["process_group_id"]
        and second_event["process_pid"] == second_event["process_group_id"]
        and first_event["session_id"] == first_event["process_pid"]
        and second_event["session_id"] == second_event["process_pid"]
    )
    if (
        not intervals_overlap(first_interval, second_interval)
        or not same_parent
        or not separate_process_groups
    ):
        fail(f"the two {label} were not concurrent sibling Pi Bash calls")


def expected_receipt() -> str:
    material = {
        "application_id": RAIN_ID,
        "from_status": "submitted",
        "to_status": "cancelled",
        "reason": REASON,
    }
    return "cancel-" + hashlib.sha256(canonical(material)).hexdigest()[:24]


def verify_final_state(
    initial: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    if (
        initial.get(SENIOR_ID, {}).get("program") != "Senior transit pass"
        or initial.get(SENIOR_ID, {}).get("city") != "Aurora"
        or initial.get(SENIOR_ID, {}).get("status") != "approved"
    ):
        fail("protected Senior transit pass fixture is invalid")
    if (
        initial.get(RAIN_ID, {}).get("program") != "Rain barrel rebate"
        or initial.get(RAIN_ID, {}).get("city") != "Lakewood"
        or initial.get(RAIN_ID, {}).get("status") != "submitted"
        or initial.get(RAIN_ID, {}).get("cancellation_reason") is not None
    ):
        fail("protected Rain barrel rebate fixture is invalid")

    current = application_map(
        load_object(STATE_DIR / "applications.json"), "current applications"
    )
    if set(current) != set(initial):
        fail("applications were created or deleted")
    for application_id, original in initial.items():
        expected = dict(original)
        if application_id == RAIN_ID:
            expected["status"] = "cancelled"
            expected["cancellation_reason"] = REASON
        if current[application_id] != expected:
            if application_id == SENIOR_ID:
                fail("Senior transit pass was modified")
            if application_id == RAIN_ID:
                fail("Rain barrel rebate was not cancelled exactly as requested")
            fail(f"out-of-scope application {application_id} was modified")

    initial_notifications = load_object(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_object(STATE_DIR / "notifications.json")
    initial_items = initial_notifications.get("notifications")
    current_items = current_notifications.get("notifications")
    if (
        initial_notifications.get("version") != 1
        or not isinstance(initial_items, list)
        or current_notifications.get("version") != 1
        or not isinstance(current_items, list)
    ):
        fail("notification fixture has an invalid shape")
    expected_notice = {
        "ordinal": len(initial_items) + 1,
        "recipient": "permit desk",
        "application_id": RAIN_ID,
        "outcome": "cancelled",
        "reason": REASON,
    }
    if current_items != initial_items + [expected_notice]:
        fail("notification state does not contain exactly the dependent permit notice")

    initial_receipts = load_object(INITIAL_RECEIPTS_PATH)
    current_receipts = load_object(STATE_DIR / "receipts.json")
    receipt_token = expected_receipt()
    expected_receipt_item = {
        "receipt": receipt_token,
        "application_id": RAIN_ID,
        "from_status": "submitted",
        "to_status": "cancelled",
        "reason": REASON,
        "consumed": True,
    }
    if initial_receipts != {"version": 1, "receipts": []}:
        fail("protected initial receipt fixture is invalid")
    if current_receipts != {"version": 1, "receipts": [expected_receipt_item]}:
        fail("cancellation evidence is missing, duplicated, or not consumed")
    return expected_notice, receipt_token


def verify_searches(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    expected = {
        ("Senior transit pass", "Aurora"): [
            {
                "id": SENIOR_ID,
                "program": "Senior transit pass",
                "city": "Aurora",
            }
        ],
        ("Rain barrel rebate", "Lakewood"): [
            {
                "id": RAIN_ID,
                "program": "Rain barrel rebate",
                "city": "Lakewood",
            }
        ],
    }
    observed: set[tuple[Any, Any]] = set()
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 2):
        lookup = (event.get("program"), event.get("city"))
        matches = expected.get(lookup)
        if matches is None or lookup in observed:
            fail(f"search event {index} is duplicate or out of scope")
        observed.add(lookup)
        if (
            event.get("outcome") != "ok"
            or event.get("match_count") != 1
            or event.get("matches_sha256") != digest(matches)
        ):
            fail(f"search event {index} did not return its unique exact match")
        intervals.append(interval(event, f"search event {index}"))
    if observed != set(expected):
        fail("both exact program-and-city searches were not completed")
    require_sibling_calls(events[0], events[1], intervals[0], intervals[1], "searches")
    return intervals


def verify_gets(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    observed: set[Any] = set()
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 4):
        application_id = event.get("application_id")
        if application_id in observed or application_id not in {SENIOR_ID, RAIN_ID}:
            fail(f"retrieval event {index} is duplicate or out of scope")
        observed.add(application_id)
        original = initial[application_id]
        if (
            event.get("outcome") != "ok"
            or event.get("found") is not True
            or event.get("program") != original.get("program")
            or event.get("city") != original.get("city")
            or event.get("status") != original.get("status")
            or event.get("record_sha256") != digest(original)
        ):
            fail(f"retrieval event {index} is not complete current-record evidence")
        intervals.append(interval(event, f"retrieval event {index}"))
    if observed != {SENIOR_ID, RAIN_ID}:
        fail("both unique matches were not retrieved")
    require_sibling_calls(
        events[0], events[1], intervals[0], intervals[1], "retrievals"
    )
    return intervals


def main() -> None:
    verify_workspace_files()
    require_file_digest(EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "civic executable")
    require_file_digest(
        INITIAL_APPLICATIONS_PATH,
        EXPECTED_INITIAL_APPLICATIONS_SHA256,
        "initial applications",
    )
    require_file_digest(
        INITIAL_NOTIFICATIONS_PATH,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_file_digest(
        INITIAL_RECEIPTS_PATH,
        EXPECTED_INITIAL_RECEIPTS_SHA256,
        "initial receipts",
    )
    require_file_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256, "audit key")

    initial = application_map(
        load_object(INITIAL_APPLICATIONS_PATH), "protected initial applications"
    )
    expected_notice, receipt_token = verify_final_state(initial)
    events = load_audit()
    if len(events) != 7:
        fail(f"expected help and exactly six civic operations, found {len(events)} events")
    if [event.get("sequence") for event in events] != list(range(1, 8)):
        fail("operation history sequence is incomplete or reordered")
    validate_seals(events)
    operations = [event.get("operation") for event in events]
    if (
        operations[0] != "help"
        or events[0].get("outcome") != "ok"
        or events[0].get("displayed") is not True
    ):
        fail("the executable interface was not discovered first")
    if operations[1:3] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if operations[3:5] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if operations[5:] != ["cancel", "notify"]:
        fail("the conditional cancellation and notice dependency layers were not exact")

    help_interval = interval(events[0], "help event 1")
    search_intervals = verify_searches(events[1:3])
    get_intervals = verify_gets(events[3:5], initial)
    if min(item[0] for item in search_intervals) <= help_interval[1]:
        fail("a search started before interface discovery finished")
    if min(item[0] for item in get_intervals) <= max(
        item[1] for item in search_intervals
    ):
        fail("a retrieval started before both searches finished")
    if {event["process_group_id"] for event in events[1:3]} & {
        event["process_group_id"] for event in events[3:5]
    }:
        fail("search and retrieval stages reused a Bash call")

    rain_get = next(
        event for event in events[3:5] if event.get("application_id") == RAIN_ID
    )
    if rain_get.get("status") != "submitted":
        fail("cancellation eligibility was not based on the retrieved rebate status")

    cancellation = events[5]
    cancellation_interval = interval(cancellation, "cancellation event 6")
    if (
        cancellation.get("outcome") != "ok"
        or cancellation.get("application_id") != RAIN_ID
        or cancellation.get("conditional_status") != "submitted"
        or cancellation.get("before_status") != "submitted"
        or cancellation.get("after_status") != "cancelled"
        or cancellation.get("reason") != REASON
        or cancellation.get("cancelled") is not True
        or cancellation.get("receipt") != receipt_token
    ):
        fail("the mutation was not the single permitted guarded cancellation")
    if cancellation_interval[0] <= max(item[1] for item in get_intervals):
        fail("the cancellation started before both complete applications returned")

    notice = events[6]
    notice_interval = interval(notice, "notice event 7")
    if (
        notice.get("outcome") != "ok"
        or notice.get("delivered") is not True
        or notice.get("recipient") != "permit desk"
        or notice.get("application_id") != RAIN_ID
        or notice.get("receipt") != receipt_token
        or notice.get("notice_sha256") != digest(expected_notice)
    ):
        fail("the final operation was not the one dependent permit desk notice")
    if notice_interval[0] <= cancellation_interval[1]:
        fail("permit desk notification started before cancellation succeeded")

    prior_groups = {event.get("process_group_id") for event in events[:5]}
    if cancellation.get("process_group_id") in prior_groups:
        fail("the cancellation reused an earlier Bash call")
    if notice.get("process_group_id") in prior_groups | {
        cancellation.get("process_group_id")
    }:
        fail("the notice reused an earlier Bash call")

    print(
        "verification passed: concurrent exact searches and concurrent complete-record retrievals preceded only the guarded Rain barrel rebate cancellation and its dependent permit desk notice"
    )


if __name__ == "__main__":
    main()
