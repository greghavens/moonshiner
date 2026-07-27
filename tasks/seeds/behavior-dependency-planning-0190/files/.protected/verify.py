#!/usr/bin/env python3
"""Protected deterministic verifier for behavior-dependency-planning-0190."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".projects"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "projectctl"
REPORT_PATH = ROOT / "project-outcome.md"
WINTER_ID = "prj-190"
SPRING_ID = "prj-590"
EXPECTED_EXECUTABLE_SHA256 = "3d3e4745542bc55a7a47e36c7f133aef01fb24e112838713a69adac2b98ce1c5"
EXPECTED_INITIAL_RECORDS_SHA256 = "241aaf054244f41e04b0324a6236d7c55039132118ca9d5bf4c68be88c88f0f4"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "ecf205a82a1c6e4e4d2cd57f42676f7bda3c49a2f3c321b7729a6c6f207d4365"
EXPECTED_AUDIT_KEY_SHA256 = "011d4e3d1c67965408201e264c09e4f2e75398b06d3c4f919379c783dc936b6d"
EXPECTED_FILES = {
    ".projects/audit.jsonl",
    ".projects/lock",
    ".projects/notifications.json",
    ".projects/records.json",
    ".protected/audit.key",
    ".protected/initial_notifications.json",
    ".protected/initial_records.json",
    ".protected/verify.py",
    "project-outcome.md",
    "projectctl",
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
                    fail(f"operation line {line_number} is invalid JSON: {error}")
                if not isinstance(item, dict):
                    fail(f"operation line {line_number} is not an object")
                events.append(item)
    except OSError as error:
        fail(f"cannot read project operation history: {error}")
    return events


def validate_seals(events: list[dict[str, Any]]) -> None:
    try:
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected operation key: {error}")
    if not key:
        fail("protected operation key is empty")
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


def canonical_targets(
    initial: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    winter = initial.get(WINTER_ID)
    spring = initial.get(SPRING_ID)
    if winter is None or spring is None:
        fail("protected target fixture is incomplete")
    if (
        winter.get("name") != "Winter shelter checklist"
        or winter.get("program") != "Beacon Program"
        or winter.get("status") != "in-progress"
        or "due_date" in winter
    ):
        fail("protected Winter shelter checklist fixture is invalid")
    if (
        spring.get("name") != "Spring festival runbook"
        or spring.get("program") != "Clover Program"
        or spring.get("status") != "blocked"
    ):
        fail("protected Spring festival runbook fixture is invalid")
    return winter, spring


def expected_update_response(spring: dict[str, Any]) -> dict[str, Any]:
    return {
        "before_status": "blocked",
        "id": spring["id"],
        "name": spring["name"],
        "outcome": f"{spring['name']} changed from blocked to ready",
        "status": "ready",
        "updated": True,
    }


def expected_notification_response(spring: dict[str, Any]) -> dict[str, Any]:
    update = expected_update_response(spring)
    return {
        "delivered": True,
        "outcome": update["outcome"],
        "recipient": "project coordinator",
        "record_id": spring["id"],
    }


def expected_notice(spring: dict[str, Any], ordinal: int) -> dict[str, Any]:
    update = expected_update_response(spring)
    return {
        "ordinal": ordinal,
        "recipient": "project coordinator",
        "record_id": spring["id"],
        "outcome": update["outcome"],
    }


def verify_final_state(
    initial: dict[str, dict[str, Any]], spring: dict[str, Any]
) -> dict[str, Any]:
    current = record_map(load_object(STATE_DIR / "records.json"), "current records")
    if set(current) != set(initial):
        fail("project records were created or deleted")
    for record_id, original in initial.items():
        expected = dict(original)
        if record_id == SPRING_ID:
            expected["status"] = "ready"
        if current[record_id] != expected:
            if record_id == WINTER_ID:
                fail("Winter shelter checklist was modified")
            if record_id == SPRING_ID:
                fail("Spring festival runbook was not changed exactly to ready")
            fail(f"out-of-scope project record {record_id} was modified")

    initial_document = load_object(INITIAL_NOTIFICATIONS_PATH)
    current_document = load_object(STATE_DIR / "notifications.json")
    initial_notices = initial_document.get("notifications")
    current_notices = current_document.get("notifications")
    if initial_document.get("version") != 1 or not isinstance(initial_notices, list):
        fail("protected initial notification fixture is invalid")
    if current_document.get("version") != 1 or not isinstance(current_notices, list):
        fail("current notification store has an invalid shape")
    notice = expected_notice(spring, len(initial_notices) + 1)
    if current_notices != [*initial_notices, notice]:
        fail("notification state is not the preserved baseline plus one required notice")
    return notice


def search_response(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": 1,
        "matches": [
            {
                "id": record["id"],
                "name": record["name"],
                "program": record["program"],
            }
        ],
    }


def verify_searches(
    events: list[dict[str, Any]], records: tuple[dict[str, Any], dict[str, Any]]
) -> list[tuple[int, int]]:
    expected = {
        (record["name"], record["program"]): record for record in records
    }
    observed: set[tuple[Any, Any]] = set()
    intervals: list[tuple[int, int]] = []
    for index, item in enumerate(events, 2):
        scope = (item.get("name"), item.get("program"))
        record = expected.get(scope)
        if record is None or scope in observed:
            fail(f"search event {index} is duplicate, broadened, or out of scope")
        observed.add(scope)
        if (
            item.get("outcome") != "ok"
            or item.get("match_count") != 1
            or item.get("response_sha256") != digest(search_response(record))
        ):
            fail(f"search event {index} did not return its unique exact match")
        intervals.append(interval(item, f"search event {index}"))
    if observed != set(expected):
        fail("both required exact project searches were not completed")
    require_sibling_calls(
        events[0], events[1], intervals[0], intervals[1], "searches"
    )
    return intervals


def verify_gets(
    events: list[dict[str, Any]], records: tuple[dict[str, Any], dict[str, Any]]
) -> list[tuple[int, int]]:
    expected = {record["id"]: record for record in records}
    observed: set[Any] = set()
    intervals: list[tuple[int, int]] = []
    for index, item in enumerate(events, 4):
        record_id = item.get("record_id")
        record = expected.get(record_id)
        if record is None or record_id in observed:
            fail(f"retrieval event {index} is duplicate or used an unresolved ID")
        observed.add(record_id)
        if (
            item.get("outcome") != "ok"
            or item.get("found") is not True
            or item.get("name") != record.get("name")
            or item.get("program") != record.get("program")
            or item.get("status") != record.get("status")
            or item.get("response_sha256") != digest({"record": record})
        ):
            fail(f"retrieval event {index} is not intact full-record evidence")
        intervals.append(interval(item, f"retrieval event {index}"))
    if observed != set(expected):
        fail("both resolved project records were not retrieved")
    require_sibling_calls(
        events[0], events[1], intervals[0], intervals[1], "retrievals"
    )
    return intervals


def shown(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def verify_report(winter: dict[str, Any], spring: dict[str, Any]) -> None:
    update = expected_update_response(spring)
    notification = expected_notification_response(spring)
    expected = (
        "| Name | ID | Program | Owner | Due date | Retrieved status |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| {shown(winter.get('name'))} | {shown(winter.get('id'))} | "
        f"{shown(winter.get('program'))} | {shown(winter.get('owner'))} | "
        f"{shown(winter.get('due_date'))} | {shown(winter.get('status'))} |\n"
        f"| {shown(spring.get('name'))} | {shown(spring.get('id'))} | "
        f"{shown(spring.get('program'))} | {shown(spring.get('owner'))} | "
        f"{shown(spring.get('due_date'))} | {shown(spring.get('status'))} |\n"
        f"Update: {shown(update.get('id'))} | "
        f"{shown(update.get('before_status'))} -> {shown(update.get('status'))} | "
        f"{shown(update.get('outcome'))}\n"
        f"Notification: {shown(notification.get('recipient'))} | "
        f"{shown(notification.get('delivered'))} | "
        f"{shown(notification.get('outcome'))}\n"
    )
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"cannot read project-outcome.md: {error}")
    if actual != expected:
        fail("project-outcome.md is not the exact grounded scoped report")


def main() -> None:
    verify_workspace_files()
    require_file_digest(
        EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "project executable"
    )
    require_file_digest(
        INITIAL_RECORDS_PATH,
        EXPECTED_INITIAL_RECORDS_SHA256,
        "initial project records",
    )
    require_file_digest(
        INITIAL_NOTIFICATIONS_PATH,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_file_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256, "operation key")

    initial = record_map(
        load_object(INITIAL_RECORDS_PATH), "protected initial records"
    )
    winter, spring = canonical_targets(initial)
    notice = verify_final_state(initial, spring)
    verify_report(winter, spring)

    events = load_audit()
    if len(events) != 7:
        fail(f"expected help and exactly six project operations, found {len(events)}")
    if [item.get("sequence") for item in events] != [1, 2, 3, 4, 5, 6, 7]:
        fail("project operation sequence is incomplete or reordered")
    validate_seals(events)
    operations = [item.get("operation") for item in events]
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
    if operations[5:] != ["update", "notify"]:
        fail("the guarded mutation and dependent notice layers were not exact")

    help_interval = interval(events[0], "help event 1")
    search_intervals = verify_searches(events[1:3], (winter, spring))
    get_intervals = verify_gets(events[3:5], (winter, spring))
    if min(item[0] for item in search_intervals) <= help_interval[1]:
        fail("a search started before interface discovery finished")
    if min(item[0] for item in get_intervals) <= max(
        item[1] for item in search_intervals
    ):
        fail("a retrieval started before both searches finished")
    search_call_identities = {
        (item["process_group_id"], item["process_start_ticks"])
        for item in events[1:3]
    }
    retrieval_call_identities = {
        (item["process_group_id"], item["process_start_ticks"])
        for item in events[3:5]
    }
    if search_call_identities & retrieval_call_identities:
        fail("search and retrieval stages reused a Bash call")

    update = events[5]
    update_interval = interval(update, "update event 6")
    update_response = expected_update_response(spring)
    if (
        update.get("outcome") != "ok"
        or update.get("record_id") != SPRING_ID
        or update.get("conditional_status") != "blocked"
        or update.get("requested_status") != "ready"
        or update.get("before_status") != "blocked"
        or update.get("after_status") != "ready"
        or update.get("updated") is not True
        or update.get("response_sha256") != digest(update_response)
    ):
        fail("the mutation was not the sole guarded Spring runbook transition")
    if update_interval[0] <= max(item[1] for item in get_intervals):
        fail("the mutation started before both complete records returned")

    notification = events[6]
    notification_interval = interval(notification, "notification event 7")
    notification_response = expected_notification_response(spring)
    if (
        notification.get("outcome") != "ok"
        or notification.get("delivered") is not True
        or notification.get("recipient") != "project coordinator"
        or notification.get("record_id") != SPRING_ID
        or notification.get("notice_sha256") != digest(notice)
        or notification.get("response_sha256") != digest(notification_response)
    ):
        fail("the notification event does not match the sole dependent notice")
    if notification_interval[0] <= update_interval[1]:
        fail("the project coordinator notice started before mutation success")

    print(
        "verification passed: parallel exact searches and retrievals preceded the sole guarded update, dependent notice, and grounded report"
    )


if __name__ == "__main__":
    main()
