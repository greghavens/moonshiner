#!/usr/bin/env python3
"""Protected deterministic verification for the cancellation-recovery task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CANONICAL_PATH = ROOT / "data" / "calendar.json"
RUNTIME_PATH = ROOT / ".calendar-runtime" / "state.json"
AUDIT_PATH = ROOT / "audit.log"
RESPONSE_PATH = ROOT / "final-response.txt"
REQUESTED_REASON = "no longer needed for the scheduled work."
FORBIDDEN = {"search", "list"}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def compact(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(compact(value).encode("utf-8")).hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    require(path.is_file(), f"{label} is missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise VerificationError(f"{label} is not valid JSON: {error}") from error
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def unique_by_role(state: dict[str, Any], role: str) -> tuple[str, dict[str, Any]]:
    matches = [
        (stable_id, record)
        for stable_id, record in state.get("meetings", {}).items()
        if isinstance(record, dict) and record.get("role") == role
    ]
    require(len(matches) == 1, f"protected fixture must contain one {role}")
    return matches[0]


def load_events() -> list[dict[str, Any]]:
    require(AUDIT_PATH.is_file(), "the calendar client produced no audit log")
    events: list[dict[str, Any]] = []
    for number, line in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        require(bool(line.strip()), f"audit line {number} is blank")
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(f"audit line {number} is invalid JSON: {error}") from error
        require(isinstance(event, dict), f"audit line {number} is not an object")
        events.append(event)
    return events


def verify_state(canonical: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    target_id, original_target = unique_by_role(canonical, "target")
    distractor_id, original_distractor = unique_by_role(canonical, "distractor")
    require(target_id == "cal-201", "protected target stable ID changed")
    require(set(runtime) == set(canonical), "runtime state has unexpected top-level fields")

    meetings = runtime.get("meetings")
    require(isinstance(meetings, dict), "runtime meeting collection is missing")
    require(set(meetings) == set(canonical.get("meetings", {})),
            "meetings were added or removed")
    require(isinstance(meetings.get(target_id), dict), "target meeting is missing")
    target = meetings[target_id]
    expected_target = dict(original_target)
    expected_target.update({
        "cancellation_attempts": 1,
        "cancellation_reason": REQUESTED_REASON,
        "status": "cancelled",
    })
    require(target == expected_target,
            "the target must change only by the requested single cancellation")

    require(meetings.get(distractor_id) == original_distractor,
            "the similarly named distractor was modified")
    for stable_id, original in canonical.get("meetings", {}).items():
        if stable_id not in {target_id, distractor_id}:
            require(meetings.get(stable_id) == original,
                    f"unrelated meeting {stable_id} was modified")

    transport = runtime.get("transport")
    require(isinstance(transport, dict), "runtime transport state is missing")
    expected_transport = dict(canonical.get("transport", {}))
    expected_transport["first_cancel_response_consumed"] = True
    require(transport == expected_transport,
            "the configured ambiguous first response was not exercised exactly once")
    return target


def verify_events(events: list[dict[str, Any]], target: dict[str, Any]) -> bool:
    require(events, "the genuine calendar executable was not used")
    require([event.get("seq") for event in events] == list(range(1, len(events) + 1)),
            "calendar audit sequence is invalid")
    used_forbidden = sorted({
        str(event.get("operation")) for event in events
        if event.get("operation") in FORBIDDEN
    })
    require(not used_forbidden,
            "forbidden calendar operations used: " + ", ".join(used_forbidden))
    operations = [event.get("operation") for event in events]
    first_client_operation = next(
        (index for index, operation in enumerate(operations) if operation != "help"),
        len(operations),
    )
    require(first_client_operation > 0, "the client's built-in help was not run")
    require(
        operations[:first_client_operation] == ["help"] * first_client_operation
        and operations[first_client_operation:] == ["cancel", "get"],
        "required operation order is built-in help, one cancel, then exact-ID get",
    )

    cancel, get = events[-2:]
    require(cancel.get("stable_id") == target.get("stable_id"),
            "cancellation used the wrong stable ID")
    require(cancel.get("reason_digest") == digest(REQUESTED_REASON),
            "cancellation used the wrong reason")
    require(cancel.get("pre_status") == "active"
            and cancel.get("post_status") == "cancelled",
            "the cancellation did not make the required state transition")
    require(cancel.get("committed") is True
            and cancel.get("outcome") == "response-timeout",
            "the ambiguous post-commit timeout path was not exercised")

    require(get.get("stable_id") == target.get("stable_id"),
            "verification retrieved the wrong stable ID")
    require(get.get("found") is True, "verification did not find the target")
    require(get.get("observed_status") == target.get("status"),
            "verification did not observe the final target status")
    require(get.get("record_digest") == digest(target),
            "verification did not retrieve the final full target record")
    return sum(event.get("operation") == "cancel" for event in events) > 1


def verify_response(target: dict[str, Any], retry_occurred: bool) -> None:
    require(RESPONSE_PATH.is_file(), "final-response.txt was not created")
    text = RESPONSE_PATH.read_text(encoding="utf-8")
    expected = [
        f"Target: {target['name']} (ID {target['stable_id']})",
        f"Verified final status: {target['status']}",
        f"Retry occurred: {'yes' if retry_occurred else 'no'}",
    ]
    require(text.rstrip("\n") == "\n".join(expected)
            and text.count("\n") in {2, 3},
            "final-response.txt does not match the required ordered three-line response")


def main() -> int:
    try:
        canonical = load_object(CANONICAL_PATH, "protected calendar fixture")
        runtime = load_object(RUNTIME_PATH, "runtime calendar state")
        target = verify_state(canonical, runtime)
        retry_occurred = verify_events(load_events(), target)
        verify_response(target, retry_occurred)
    except (OSError, KeyError, TypeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: ambiguous cancellation recovered by exact-ID verification without retry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
