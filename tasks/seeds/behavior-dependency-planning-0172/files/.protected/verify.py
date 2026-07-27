#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0172."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".education"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "educationctl"
COMMUNITY_ID = "edu-272"
STATISTICS_ID = "edu-672"
EXPECTED_EXECUTABLE_SHA256 = "c54538334789b3aa53e0e9f396473bfaff1fe01e3a88352a3c514fecaade655e"
EXPECTED_INITIAL_RECORDS_SHA256 = "6cb09c17e048631491f426ece4453f3fee55dbc9a4754e2524924b076b3b4a62"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "152e64b023af00db83befd18aea91d7976399d7c3bc5f091c97e0814b1e7d73f"
EXPECTED_AUDIT_KEY_SHA256 = "539a2c0742800502c910f36fad439d4bfa906ef41a81c6740f3196a7679f6c9e"
EXPECTED_FILES = {
    ".education/audit.jsonl",
    ".education/lock",
    ".education/notifications.json",
    ".education/records.json",
    ".protected/audit.key",
    ".protected/initial_notifications.json",
    ".protected/initial_records.json",
    ".protected/verify.py",
    "educationctl",
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


def record_map(document: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    records = document.get("records")
    if document.get("version") != 1 or not isinstance(records, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and bool(record["id"])
        for record in records
    ):
        fail(f"{label} contains an invalid record")
    result = {record["id"]: record for record in records}
    if len(result) != len(records):
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
                    item = json.loads(line)
                except json.JSONDecodeError as error:
                    fail(f"audit line {line_number} is invalid JSON: {error}")
                if not isinstance(item, dict):
                    fail(f"audit line {line_number} is not an object")
                events.append(item)
    except OSError as error:
        fail(f"cannot read education operation history: {error}")
    return events


def validate_seals(events: list[dict[str, Any]]) -> None:
    try:
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    if not key:
        fail("protected audit key is empty")
    for index, item in enumerate(events, 1):
        seal = item.get("seal")
        if not isinstance(seal, str):
            fail(f"operation event {index} has no seal")
        unsigned = dict(item)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, expected):
            fail(f"operation event {index} has an invalid seal")


def interval(item: dict[str, Any], label: str) -> tuple[int, int]:
    started = item.get("started_ns")
    finished = item.get("finished_ns")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(finished, int)
        or isinstance(finished, bool)
        or started >= finished
    ):
        fail(f"{label} has an invalid execution interval")
    return started, finished


def overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def require_sibling_calls(
    first_event: dict[str, Any],
    second_event: dict[str, Any],
    first_interval: tuple[int, int],
    second_interval: tuple[int, int],
    label: str,
) -> None:
    for index, item in enumerate((first_event, second_event), 1):
        for field in ("process_pid", "process_group_id", "session_id", "parent_pid"):
            value = item.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                fail(f"{label} call {index} has invalid process evidence")
        for field in ("process_start_ticks", "parent_start_ticks"):
            value = item.get(field)
            if not isinstance(value, str) or not value or value == "unavailable":
                fail(f"{label} call {index} lacks stable process evidence")
    same_parent = (
        first_event["parent_pid"] == second_event["parent_pid"]
        and first_event["parent_start_ticks"]
        == second_event["parent_start_ticks"]
    )
    separate_process_groups = (
        first_event["process_group_id"] != second_event["process_group_id"]
        and first_event["process_pid"] == first_event["process_group_id"]
        and second_event["process_pid"] == second_event["process_group_id"]
        and first_event["session_id"] == first_event["process_pid"]
        and second_event["session_id"] == second_event["process_pid"]
    )
    if (
        not overlap(first_interval, second_interval)
        or not same_parent
        or not separate_process_groups
    ):
        fail(f"the two {label} were not concurrent sibling Pi Bash calls")


def verify_final_state(initial: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if (
        initial.get(COMMUNITY_ID, {}).get("name") != "Community Mediation"
        or initial.get(COMMUNITY_ID, {}).get("location") != "Downtown Campus"
        or initial.get(COMMUNITY_ID, {}).get("status") != "open"
    ):
        fail("protected Community Mediation fixture is invalid")
    if (
        initial.get(STATISTICS_ID, {}).get("name") != "Statistics Refresher"
        or initial.get(STATISTICS_ID, {}).get("location") != "North Campus"
        or initial.get(STATISTICS_ID, {}).get("status") != "waitlisted"
    ):
        fail("protected Statistics Refresher fixture is invalid")

    current = record_map(load_object(STATE_DIR / "records.json"), "current records")
    if set(current) != set(initial):
        fail("records were created or deleted")
    for record_id, original in initial.items():
        expected = dict(original)
        if record_id == STATISTICS_ID:
            expected["status"] = "enrollment-ready"
        if current[record_id] != expected:
            if record_id == COMMUNITY_ID:
                fail("Community Mediation was modified")
            if record_id == STATISTICS_ID:
                fail("Statistics Refresher was not changed exactly as requested")
            fail(f"out-of-scope record {record_id} was modified")

    initial_notices = load_object(INITIAL_NOTIFICATIONS_PATH)
    current_notices = load_object(STATE_DIR / "notifications.json")
    if initial_notices.get("version") != 1 or initial_notices.get("notifications") != []:
        fail("protected initial notification fixture is invalid")
    notices = current_notices.get("notifications")
    if current_notices.get("version") != 1 or not isinstance(notices, list):
        fail("current notification store has an invalid shape")
    if len(notices) != 1 or not isinstance(notices[0], dict):
        fail("exactly one registrar notice was not created")
    notice = notices[0]
    if (
        notice.get("ordinal") != 1
        or notice.get("recipient") != "registrar desk"
        or notice.get("record_id") != STATISTICS_ID
        or not isinstance(notice.get("subject"), str)
        or not notice["subject"].strip()
        or not isinstance(notice.get("message"), str)
        or "Statistics Refresher" not in notice["message"]
        or "enrollment-ready" not in notice["message"]
    ):
        fail("the sole notice does not report the required successful outcome")
    return notice


def verify_searches(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    expected = {
        ("Community Mediation", "Downtown Campus"): [
            {
                "id": COMMUNITY_ID,
                "name": "Community Mediation",
                "location": "Downtown Campus",
            }
        ],
        ("Statistics Refresher", "North Campus"): [
            {
                "id": STATISTICS_ID,
                "name": "Statistics Refresher",
                "location": "North Campus",
            }
        ],
    }
    observed: set[tuple[Any, Any]] = set()
    intervals: list[tuple[int, int]] = []
    for index, item in enumerate(events, 1):
        lookup = (item.get("name"), item.get("location"))
        matches = expected.get(lookup)
        if matches is None or lookup in observed:
            fail(f"search event {index} is duplicate or out of scope")
        observed.add(lookup)
        if (
            item.get("outcome") != "ok"
            or item.get("match_count") != 1
            or item.get("matches_sha256") != digest(matches)
        ):
            fail(f"search event {index} did not return its unique exact match")
        intervals.append(interval(item, f"search event {index}"))
    if observed != set(expected):
        fail("both required exact lookups were not completed")
    require_sibling_calls(
        events[0], events[1], intervals[0], intervals[1], "searches"
    )
    return intervals


def verify_gets(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    observed: set[Any] = set()
    intervals: list[tuple[int, int]] = []
    for index, item in enumerate(events, 3):
        record_id = item.get("record_id")
        if record_id in observed or record_id not in {COMMUNITY_ID, STATISTICS_ID}:
            fail(f"retrieval event {index} is duplicate or out of scope")
        observed.add(record_id)
        original = initial[record_id]
        if (
            item.get("outcome") != "ok"
            or item.get("found") is not True
            or item.get("name") != original.get("name")
            or item.get("location") != original.get("location")
            or item.get("status") != original.get("status")
            or item.get("record_sha256") != digest(original)
        ):
            fail(f"retrieval event {index} is not intact current-record evidence")
        intervals.append(interval(item, f"retrieval event {index}"))
    if observed != {COMMUNITY_ID, STATISTICS_ID}:
        fail("both unique matches were not retrieved")
    require_sibling_calls(
        events[0], events[1], intervals[0], intervals[1], "retrievals"
    )
    return intervals


def main() -> None:
    verify_workspace_files()
    require_file_digest(
        EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "education executable"
    )
    require_file_digest(
        INITIAL_RECORDS_PATH,
        EXPECTED_INITIAL_RECORDS_SHA256,
        "initial records",
    )
    require_file_digest(
        INITIAL_NOTIFICATIONS_PATH,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_file_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256, "audit key")

    initial = record_map(
        load_object(INITIAL_RECORDS_PATH), "protected initial records"
    )
    notice = verify_final_state(initial)
    events = load_audit()
    if len(events) != 7:
        fail(f"expected help and exactly six education operations, found {len(events)} events")
    if [item.get("sequence") for item in events] != [1, 2, 3, 4, 5, 6, 7]:
        fail("education operation sequence is incomplete or reordered")
    validate_seals(events)
    operations = [item.get("operation") for item in events]
    if operations[0] != "help" or events[0].get("outcome") != "ok" or events[0].get("displayed") is not True:
        fail("the executable interface was not discovered first")
    if operations[1:3] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if operations[3:5] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if operations[5:] != ["update", "notify"]:
        fail("the mutation and notice dependency layers were not exact")

    help_interval = interval(events[0], "help event 1")
    search_intervals = verify_searches(events[1:3], initial)
    get_intervals = verify_gets(events[3:5], initial)
    if min(item[0] for item in search_intervals) <= help_interval[1]:
        fail("a search started before interface discovery finished")
    if min(item[0] for item in get_intervals) <= max(
        item[1] for item in search_intervals
    ):
        fail("a retrieval started before both searches finished")
    if {
        item["process_group_id"] for item in events[1:3]
    } & {
        item["process_group_id"] for item in events[3:5]
    }:
        fail("search and retrieval stages reused a Bash call")

    update = events[5]
    update_interval = interval(update, "update event 6")
    if (
        update.get("outcome") != "ok"
        or update.get("record_id") != STATISTICS_ID
        or update.get("conditional_status") != "waitlisted"
        or update.get("before_status") != "waitlisted"
        or update.get("after_status") != "enrollment-ready"
        or update.get("updated") is not True
    ):
        fail("the mutation was not the permitted guarded Statistics transition")
    if update_interval[0] <= max(item[1] for item in get_intervals):
        fail("the mutation started before both complete records returned")

    notification = events[6]
    notification_interval = interval(notification, "notification event 7")
    if (
        notification.get("outcome") != "ok"
        or notification.get("delivered") is not True
        or notification.get("recipient") != "registrar desk"
        or notification.get("record_id") != STATISTICS_ID
        or notification.get("notice_sha256") != digest(notice)
    ):
        fail("the notification event does not match the sole registrar notice")
    if notification_interval[0] <= update_interval[1]:
        fail("the registrar notice started before the mutation succeeded")

    print(
        "verification passed: parallel exact searches and parallel retrievals preceded the sole guarded update and dependent registrar notice"
    )


if __name__ == "__main__":
    main()
