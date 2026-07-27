#!/usr/bin/env python3
"""Protected deterministic verifier for the project availability task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "projects.json"
NOTIFICATIONS_PATH = ROOT / "data" / "notifications.jsonl"
LEDGER_PATH = ROOT / ".project-audit" / "session.jsonl"
REPORT_PATH = ROOT / "availability_report.txt"
SIGNING_KEY = b"moonshiner-pi-project-availability-v1-7c39d0a5"
EXPECTED_DATA_SHA256 = "1e3ebbfd3738413ca8138def340ccb9d5ddb9d4d456964753e1d826a1e827d35"
EXPECTED_NOTIFICATIONS_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
TARGETS = (
    {"name": "Policy review", "location": "Beacon", "date": "2026-09-21"},
    {"name": "Newsletter draft", "location": "Clover", "date": "2026-09-21"},
)
TRANSIENT_RESULT = {
    "error": "transient_service_overload",
    "retryable": True,
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sign(record: dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()


def load_expected() -> list[dict[str, Any]]:
    try:
        data_bytes = DATA_PATH.read_bytes()
        notifications_bytes = NOTIFICATIONS_PATH.read_bytes()
    except OSError as error:
        raise VerificationError(f"cannot read protected project data: {error}") from error
    require(
        hashlib.sha256(data_bytes).hexdigest() == EXPECTED_DATA_SHA256,
        "protected project data was modified",
    )
    require(
        hashlib.sha256(notifications_bytes).hexdigest()
        == EXPECTED_NOTIFICATIONS_SHA256,
        "the read-only check must not send notifications",
    )
    try:
        data = json.loads(data_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"protected project data is invalid: {error}") from error

    expected: list[dict[str, Any]] = []
    projects = data.get("projects")
    require(isinstance(projects, list), "protected projects collection is invalid")
    for target in TARGETS:
        matches = [
            project
            for project in projects
            if isinstance(project, dict)
            and project.get("name") == target["name"]
            and project.get("location") == target["location"]
        ]
        require(len(matches) == 1, "a requested project does not resolve uniquely")
        available = matches[0].get("availability", {}).get(target["date"])
        require(isinstance(available, bool), "requested availability is not configured")
        expected.append({**target, "available": available})
    return expected


def load_events() -> list[dict[str, Any]]:
    require(LEDGER_PATH.is_file(), "missing command-generated project evidence")
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) >= 4, "missing help or availability evidence")
        raw_events = [json.loads(line) for line in lines]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid project evidence: {error}") from error

    events: list[dict[str, Any]] = []
    for raw_event in raw_events:
        require(isinstance(raw_event, dict), "each evidence event must be an object")
        event = dict(raw_event)
        signature = event.pop("signature", None)
        require(
            isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
            "project evidence signature mismatch",
        )
        require(event.get("version") == 1, "unsupported evidence version")
        for field in ("pid", "parent_pid", "started_ns", "finished_ns"):
            require(isinstance(event.get(field), int), f"invalid event {field}")
        require(event["started_ns"] < event["finished_ns"], "invalid event interval")
        events.append(event)
    return events


def intervals_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return max(left["started_ns"], right["started_ns"]) < min(
        left["finished_ns"], right["finished_ns"]
    )


def event_for_attempt(
    events: list[dict[str, Any]], target: dict[str, Any], attempt: int
) -> dict[str, Any]:
    matches = [
        event
        for event in events
        if event.get("request") == target and event.get("attempt") == attempt
    ]
    require(len(matches) == 1, f"expected one attempt {attempt} for {target['name']}")
    return matches[0]


def verify_events(events: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    actions = {event.get("action") for event in events}
    require(
        actions <= {"help", "availability"} and "availability" in actions,
        "a forbidden project operation was used",
    )
    helps = [event for event in events if event.get("action") == "help"]
    availability = [event for event in events if event.get("action") == "availability"]
    require(helps, "the client interface was not discovered from built-in help")
    require(
        len(availability) == 3,
        "exactly two initial checks and one failed-branch retry are required",
    )

    for event in helps:
        arguments = event.get("request", {}).get("arguments")
        require(
            isinstance(arguments, list)
            and all(isinstance(argument, str) for argument in arguments)
            and any(argument in {"-h", "--help"} for argument in arguments),
            "invalid built-in-help evidence",
        )
        require(
            event.get("outcome") == "success"
            and event.get("result") == {"displayed": True},
            "built-in help did not complete successfully",
        )

    first_target, second_target = TARGETS
    first_initial = event_for_attempt(availability, first_target, 1)
    second_initial = event_for_attempt(availability, second_target, 1)
    second_retry = event_for_attempt(availability, second_target, 2)
    require(
        not [event for event in availability if event.get("request") not in TARGETS],
        "an availability request targeted an unrequested item",
    )

    require(first_initial.get("outcome") == "success", "first check did not succeed")
    require(
        first_initial.get("result") == expected[0],
        "first successful result does not match protected project data",
    )
    require(
        second_initial.get("outcome") == "transient_error"
        and second_initial.get("result") == TRANSIENT_RESULT,
        "second check did not produce the configured transient error",
    )
    require(second_retry.get("outcome") == "success", "failed check was not recovered")
    require(
        second_retry.get("result") == expected[1],
        "retried result does not match protected project data",
    )

    require(
        first_initial["pid"] != second_initial["pid"],
        "initial checks need independent processes",
    )
    require(
        first_initial["parent_pid"] == second_initial["parent_pid"],
        "initial checks were not launched by one shell execution",
    )
    require(
        intervals_overlap(first_initial, second_initial),
        "initial availability checks were not concurrent",
    )
    require(
        max(first_initial["finished_ns"], second_initial["finished_ns"])
        < second_retry["started_ns"],
        "the retry began before both initial checks finished",
    )
    require(
        second_retry["parent_pid"] != first_initial["parent_pid"],
        "the retry must be issued in one later shell execution",
    )
    first_started = min(first_initial["started_ns"], second_initial["started_ns"])
    require(
        any(
            event["finished_ns"] < first_started
            and event["parent_pid"] != first_initial["parent_pid"]
            for event in helps
        ),
        "built-in help must first run in a help-only shell execution",
    )


def verify_report(expected: list[dict[str, Any]]) -> None:
    require(REPORT_PATH.is_file(), "missing availability_report.txt")
    lines = []
    for result in expected:
        word = "available" if result["available"] else "unavailable"
        lines.append(
            f"- {result['name']} at {result['location']}: {word} on {result['date']}."
        )
    wanted = "\n".join(lines) + "\n"
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise VerificationError(f"cannot read availability report: {error}") from error
    require(actual == wanted, "availability_report.txt violates data or exact format")


def main() -> int:
    try:
        expected = load_expected()
        events = load_events()
        verify_events(events, expected)
        verify_report(expected)
    except (VerificationError, KeyError, TypeError, AttributeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: concurrent partial-failure recovery and exact report verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
