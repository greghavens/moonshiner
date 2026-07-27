#!/usr/bin/env python3
"""Protected verifier for behavior-error-recovery-0004."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".messaging"
PROTECTED_DIR = ROOT / ".protected"
KEY_PATH = PROTECTED_DIR / "audit.key"

CAMPUS = (
    "Campus Snow Closure Notice",
    "2026-11-18",
    "Campus Operations",
)
SHUTTLE = (
    "Evening Shuttle Delay Notice",
    "2026-11-18",
    "Transportation Desk",
)


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_object(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path.relative_to(ROOT)}: {error}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} is not a JSON object")
    return value


def verify_read_only_state() -> None:
    pairs = [
        ("availability.json", "initial_availability.json"),
        ("messages.json", "initial_messages.json"),
        ("preferences.json", "initial_preferences.json"),
        ("notifications.json", "initial_notifications.json"),
        ("mutations.json", "initial_mutations.json"),
    ]
    for current_name, initial_name in pairs:
        current = load_object(STATE_DIR / current_name)
        initial = load_object(PROTECTED_DIR / initial_name)
        if current != initial:
            fail(f"read-only state changed: {current_name}")


def load_audit() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with (STATE_DIR / "audit.jsonl").open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    fail(f"audit line {line_number} is invalid JSON: {error}")
                if not isinstance(event, dict):
                    fail(f"audit line {line_number} is not an object")
                events.append(event)
    except OSError as error:
        fail(f"cannot read audit log: {error}")
    return events


def validate_seals(events: list[dict[str, Any]]) -> None:
    try:
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        if not isinstance(seal, str):
            fail(f"audit event {index} has no seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, expected):
            fail(f"audit event {index} has an invalid seal")


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


def scope(event: dict[str, Any]) -> tuple[Any, Any, Any]:
    return event.get("name"), event.get("date"), event.get("location")


def overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def verify_attempt_ledger() -> None:
    attempts_document = load_object(STATE_DIR / "attempts.json")
    attempts = attempts_document.get("attempts")
    if attempts_document.get("version") != 1 or not isinstance(attempts, list):
        fail("attempt ledger has an invalid shape")
    observed: dict[tuple[Any, Any, Any], Any] = {}
    for entry in attempts:
        if not isinstance(entry, dict):
            fail("attempt ledger contains a non-object entry")
        key = (entry.get("name"), entry.get("date"), entry.get("location"))
        if key in observed:
            fail("attempt ledger contains a duplicate scope")
        observed[key] = entry.get("count")
    if observed != {CAMPUS: 1, SHUTTLE: 2}:
        fail("only the failed availability branch may be retried once")


def verify_success(event: dict[str, Any], label: str) -> None:
    if (
        event.get("outcome") != "ok"
        or event.get("available") is not True
        or event.get("retryable") is not False
        or event.get("may_have_committed") is not False
    ):
        fail(f"{label} did not record the required successful availability result")


def main() -> None:
    verify_read_only_state()
    verify_attempt_ledger()

    events = load_audit()
    if len(events) != 3:
        fail(f"expected exactly three messaging operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if any(event.get("operation") != "availability" for event in events):
        fail("a prohibited messaging operation was used")

    initial = [event for event in events if event.get("attempt") == 1]
    retry = [event for event in events if event.get("attempt") == 2]
    if len(initial) != 2 or len(retry) != 1:
        fail("expected two initial checks followed by one retry")
    if events[2] is not retry[0]:
        fail("the retry was not the final messaging operation")

    initial_by_scope = {scope(event): event for event in initial}
    if set(initial_by_scope) != {CAMPUS, SHUTTLE} or len(initial_by_scope) != 2:
        fail("initial availability checks were incorrect or duplicated")
    campus_event = initial_by_scope[CAMPUS]
    shuttle_failure = initial_by_scope[SHUTTLE]
    shuttle_retry = retry[0]

    verify_success(campus_event, "Campus check")
    if (
        shuttle_failure.get("outcome") != "temporary_error"
        or shuttle_failure.get("error_code") != "SERVICE_BUSY"
        or shuttle_failure.get("retryable") is not True
        or shuttle_failure.get("may_have_committed") is not False
    ):
        fail("Shuttle check did not produce the expected retry-safe temporary failure")
    if scope(shuttle_retry) != scope(shuttle_failure):
        fail("the failed branch was retried with changed arguments")
    verify_success(shuttle_retry, "Shuttle retry")

    campus_interval = interval(campus_event, "Campus initial check")
    shuttle_interval = interval(shuttle_failure, "Shuttle initial check")
    retry_interval = interval(shuttle_retry, "Shuttle retry")
    if not overlaps(campus_interval, shuttle_interval):
        fail("the independent initial availability checks did not run concurrently")
    if retry_interval[0] <= max(campus_interval[1], shuttle_interval[1]):
        fail("the retry began before both initial checks completed")

    print(
        "verification passed: independent checks overlapped, the successful branch was retained, and only the retry-safe failure was retried"
    )


if __name__ == "__main__":
    main()
