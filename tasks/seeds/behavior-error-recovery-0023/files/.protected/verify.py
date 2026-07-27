#!/usr/bin/env python3
"""Protected deterministic verifier for reservation error recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".reservation" / "state.json"
SEED_PATH = ROOT / ".protected" / "reservation_seed.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
REPORT_PATH = ROOT / "cancellation-report.txt"
TARGET_ID = "hos-123"
REASON = "record superseded by a corrected request"

PROTECTED_HASHES = {
    ".gitignore": "5fbd42dea5ef5984dbcdedc427868c37b8de7b66a0a35cc5c27fd264d7bb10dd",
    ".protected/audit.key": "2fb68fb694242880548f6c09de91c587542922562623cd33fc18b15ab9431d07",
    ".protected/reservation_seed.json": "94c20de536967102c7555319fa12b256b37848906333860b3ff3803c734d9be2",
    ".protected/setup.py": "d0c025155deb1197d45ebdf200cb16120ed61c5ab148846d760f5db74ef9e24c",
    "RESERVATIONS.md": "8d360035f1c297892a43d53092e2ecca0ce884481ef374f4e2a6123b03cbb434",
    "reservationctl": "597694a6b83c98b077bcc0eae6b40cfb84a8fd793115e6e38f80962b763c282e",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"{label} is unreadable: {error}") from error
    require(isinstance(value, dict), f"{label} is not a JSON object")
    return value


def event_seal(event: dict[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "seal"}
    key = bytes.fromhex(KEY_PATH.read_text(encoding="utf-8").strip())
    return hmac.new(
        key,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_protected_files() -> None:
    for relative, expected_hash in PROTECTED_HASHES.items():
        path = ROOT / relative
        require(path.is_file(), f"protected task file is missing: {relative}")
        require(
            file_sha256(path) == expected_hash,
            f"protected task file changed: {relative}",
        )


def expected_final_state(seed: dict[str, Any]) -> dict[str, Any]:
    expected = json.loads(json.dumps(seed))
    target = expected["reservations"][TARGET_ID]
    target["status"] = "cancelled"
    target["cancellation_reason"] = REASON
    target["version"] += 1
    expected["faults"]["cancel_timeout_after_commit"][TARGET_ID] = 0
    expected["events"] = [
        {
            "after_status": "cancelled",
            "before_status": "active",
            "operation": "cancel",
            "outcome": "timeout_after_commit",
            "reason": REASON,
            "record_id": TARGET_ID,
            "sequence": 1,
            "state_changed": True,
        },
        {
            "observed_status": "cancelled",
            "operation": "get",
            "outcome": "returned",
            "record_id": TARGET_ID,
            "sequence": 2,
        },
    ]
    for event in expected["events"]:
        event["seal"] = event_seal(event)
    expected["next_event_sequence"] = 3
    return expected


def verify_events(events: Any) -> None:
    require(isinstance(events, list), "the authenticated operation journal is missing")
    require(
        len(events) == 2,
        "expected exactly one cancellation followed by one direct retrieval",
    )
    require(
        [event.get("sequence") for event in events] == [1, 2],
        "operation sequence is malformed",
    )
    require(
        [event.get("operation") for event in events] == ["cancel", "get"],
        "the uncertain cancellation must be followed directly by a get",
    )
    for event in events:
        signature = event.get("seal")
        require(
            isinstance(signature, str)
            and hmac.compare_digest(signature, event_seal(event)),
            "operation evidence was not authentically created by reservationctl",
        )


def verify_report() -> None:
    require(REPORT_PATH.is_file(), "cancellation-report.txt is missing")
    expected = (
        "hos-123 verified status: cancelled.\n"
        "Verification made a retry necessary: no.\n"
    )
    require(
        REPORT_PATH.read_text(encoding="utf-8") == expected,
        "cancellation-report.txt is not the exact grounded two-line report",
    )


def main() -> int:
    try:
        verify_protected_files()
        seed = load_json(SEED_PATH, "protected reservation seed")
        state = load_json(STATE_PATH, "reservation runtime state")
        verify_events(state.get("events"))
        require(
            state == expected_final_state(seed),
            "reservation state does not prove the exact cancel-then-fetch workflow",
        )
        verify_report()
    except (VerificationError, KeyError, OSError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: hos-123 was cancelled once, verified by ID, and required no retry"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
