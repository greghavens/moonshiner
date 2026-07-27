#!/usr/bin/env python3
"""Protected verifier for behavior-missing-function-0014."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPENSECTL = ROOT / "expensectl"
AUDIT_KEY = ROOT / ".harness" / "audit.key"
SEED = ROOT / ".expense-seed" / "records.json"
RUNTIME = ROOT / ".expense-state" / "registry.json"
REPORT = ROOT / "cancellation-result.json"
EXPECTED_EXPENSECTL_SHA256 = (
    "fe3c6fdfc0cd86ddc789509667f6b91dae70366a05f154570726ba27e7634447"
)
EXPECTED_AUDIT_KEY_SHA256 = (
    "4640b1c3fa581ec5fa56a0e156ef238898d4b8e19cdb913fea32efa5b1eef3e4"
)
EXPECTED_SEED_SHA256 = (
    "f711bd5b580b713cf3899f487070eab4af80b61f24129b071163c08b76c4512f"
)
TARGET_ID = "exp-114"
TARGET_NAME = "Airport rail fare"
EXACT_REASON = "the expense duplicates another submitted receipt"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def require_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise VerificationError(f"cannot read protected {label}: {error}") from error
    require(actual == expected, f"protected {label} was modified")


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise VerificationError(f"cannot read {label}: {error}") from error
    except json.JSONDecodeError as error:
        raise VerificationError(f"{label} is not valid JSON: {error}") from error
    require(isinstance(value, dict), f"{label} is not a JSON object")
    return value


def records_by_id(state: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    records = state.get("records")
    require(isinstance(records, list), f"{label} has no record list")
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        require(isinstance(record, dict), f"{label} contains a malformed record")
        record_id = record.get("id")
        require(isinstance(record_id, str), f"{label} contains a record without an ID")
        require(record_id not in indexed, f"{label} contains duplicate record IDs")
        indexed[record_id] = record
    return indexed


def validate_seals(events: list[dict[str, Any]]) -> None:
    key = AUDIT_KEY.read_bytes().strip()
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        require(isinstance(seal, str), f"operation {index} has no executable seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        require(
            hmac.compare_digest(seal, expected),
            f"operation {index} has an invalid executable seal",
        )


def validate_intervals(events: list[dict[str, Any]]) -> None:
    previous_finished: int | None = None
    process_ids: list[int] = []
    for index, event in enumerate(events, 1):
        started = event.get("started_ns")
        finished = event.get("finished_ns")
        process_id = event.get("process_id")
        require(
            isinstance(started, int)
            and not isinstance(started, bool)
            and isinstance(finished, int)
            and not isinstance(finished, bool)
            and started < finished,
            f"operation {index} has an invalid execution interval",
        )
        require(
            isinstance(process_id, int)
            and not isinstance(process_id, bool)
            and process_id > 0,
            f"operation {index} has no genuine process identity",
        )
        process_ids.append(process_id)
        if previous_finished is not None:
            require(
                previous_finished <= started,
                "expense operations overlap or are reordered",
            )
        previous_finished = finished
    require(
        len(set(process_ids)) == len(process_ids),
        "each expense interaction must execute in its own process",
    )


def verify_records(seed: dict[str, Any], runtime: dict[str, Any]) -> None:
    before = records_by_id(seed, "seed registry")
    after = records_by_id(runtime, "runtime registry")
    require(before.keys() == after.keys(), "expense identities changed")
    require(TARGET_ID in before, "protected target expense is missing")
    for expense_id, original in before.items():
        current = after[expense_id]
        if expense_id == TARGET_ID:
            expected = dict(original)
            expected["status"] = "cancelled"
            expected["cancellation_reason"] = EXACT_REASON
            require(
                current == expected,
                "exp-114 was not cancelled with exactly the supplied reason",
            )
        else:
            require(
                current == original,
                f"untargeted or similarly named record {expense_id} was modified",
            )


def verify_report(cancellation: dict[str, Any]) -> None:
    require(REPORT.is_file(), "cancellation-result.json is missing")
    expected_result = {
        "id": TARGET_ID,
        "name": TARGET_NAME,
        "reason": EXACT_REASON,
        "status": "cancelled",
    }
    expected_text = (
        json.dumps(expected_result, ensure_ascii=False, sort_keys=True) + "\n"
    )
    try:
        actual_text = REPORT.read_text(encoding="utf-8")
    except OSError as error:
        raise VerificationError(f"cannot read cancellation result: {error}") from error
    require(
        actual_text == expected_text,
        "cancellation-result.json is not the genuine executable result",
    )
    require(
        cancellation.get("result_sha256")
        == hashlib.sha256(canonical(expected_result)).hexdigest(),
        "operation evidence does not match the result file",
    )


def verify_events(runtime: dict[str, Any]) -> None:
    raw_events = runtime.get("operations")
    require(isinstance(raw_events, list), "runtime operation journal is missing")
    require(
        all(isinstance(event, dict) for event in raw_events),
        "runtime operation journal contains malformed evidence",
    )
    events: list[dict[str, Any]] = raw_events
    operations = [event.get("operation") for event in events]
    require(
        operations[:2] in (
            ["help", "capabilities"],
            ["capabilities", "help"],
        )
        and operations[2:]
        == [
            "extension.help",
            "extension.activate",
            "capabilities",
            "cancel",
        ],
        "expected capability discovery, approved activation, and cancellation only",
    )
    require(
        [event.get("sequence") for event in events] == list(range(1, 7)),
        "operation journal is incomplete or reordered",
    )
    validate_seals(events)
    validate_intervals(events)
    for index, event in enumerate(events, 1):
        require(event.get("outcome") == "ok", f"operation {index} did not succeed")

    initial_events = {
        event.get("operation"): event
        for event in events[:2]
    }
    first_help = initial_events["help"]
    before = initial_events["capabilities"]
    extension_help_event, activation, after, cancellation = events[2:]
    first_reported = first_help.get("reported")
    before_reported = before.get("reported")
    after_reported = after.get("reported")
    require(
        isinstance(first_reported, list) and "cancel" not in first_reported,
        "initial help did not demonstrate cancellation was unavailable",
    )
    require(
        isinstance(before_reported, list) and "cancel" not in before_reported,
        "initial capability report did not demonstrate the missing function",
    )
    require(
        extension_help_event.get("documented_extension") == "cancellation",
        "the genuine extension help was not consulted",
    )
    require(
        activation.get("extension") == "cancellation"
        and activation.get("before_active") is False
        and activation.get("after_active") is True
        and activation.get("changed") is True,
        "the cancellation extension was not newly and exactly activated",
    )
    require(
        isinstance(after_reported, list) and "cancel" in after_reported,
        "the later capability report did not expose cancellation",
    )
    extensions = runtime.get("extensions")
    require(
        isinstance(extensions, dict)
        and extensions.get("cancellation") is True,
        "the approved cancellation extension is not active",
    )
    require(
        cancellation.get("expense_id") == TARGET_ID
        and cancellation.get("reason") == EXACT_REASON
        and cancellation.get("output") == "cancellation-result.json"
        and cancellation.get("before_status") == "active"
        and cancellation.get("after_status") == "cancelled"
        and cancellation.get("updated") == 1,
        "the executable cancellation was not exact",
    )
    verify_report(cancellation)


def main() -> int:
    try:
        require_digest(
            EXPENSECTL, EXPECTED_EXPENSECTL_SHA256, "expensectl executable"
        )
        require_digest(AUDIT_KEY, EXPECTED_AUDIT_KEY_SHA256, "audit key")
        require_digest(SEED, EXPECTED_SEED_SHA256, "seed expense registry")
        require(RUNTIME.is_file(), "the expense registry was not used")
        seed = load_object(SEED, "seed expense registry")
        runtime = load_object(RUNTIME, "runtime expense registry")
        verify_events(runtime)
        verify_records(seed, runtime)
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: exp-114 alone was cancelled through the newly available "
        "capability with the exact reason"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
