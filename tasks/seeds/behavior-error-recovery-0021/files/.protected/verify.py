#!/usr/bin/env python3
"""Protected deterministic verifier for the calendar recovery workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".calendar-state.json"
SEED_PATH = ROOT / ".protected" / "calendar_seed.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
TARGET_ID = "cal-121"
REASON = "request withdrawn by its owner."

PROTECTED_HASHES = {
    ".gitignore": "445f59efd96ac650ee07a9bd2fddca841007dbfbb232877d0c2bf33fd48d058f",
    ".protected/audit.key": "1081bfaf641f1a6ff76702365fe8a862224e5028f30e942f28791cb25e1dd727",
    ".protected/calendar_seed.json": "ca2636b6c62a331a46f7869bd924fb9a86e55c6d0ea7216363d31a5ee5cb99c4",
    ".protected/setup.py": "6ce22236778e23bfddd5418da06f02b80e03bf2393f80e462cf44b903f7c1340",
    "CALENDAR.md": "ecc026c5d16431ee5099153df702aab02d124f5d8151e52720e1a70ae7aad681",
    "calendarctl": "4e1b399ad494ec8c1fccc441dc16b5e67d721de931152b495824e0e44fb2d89b",
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


def event_seal(event: dict[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "seal"}
    key = bytes.fromhex(KEY_PATH.read_text(encoding="utf-8").strip())
    return hmac.new(
        key,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"{label} is unreadable: {error}") from error
    require(isinstance(value, dict), f"{label} is not a JSON object")
    return value


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
    target = expected["meetings"][TARGET_ID]
    target["status"] = "cancelled"
    target["cancellation_reason"] = REASON
    target["version"] += 1
    expected["faults"]["cancel_timeout_after_commit"][TARGET_ID] = 0
    expected["events"] = [
        {
            "after_status": "cancelled",
            "before_status": "scheduled",
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
    require(len(events) == 2, "expected exactly one cancellation and one direct retrieval")
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
            "operation evidence was not authentically created by calendarctl",
        )


def main() -> int:
    try:
        verify_protected_files()
        seed = load_json(SEED_PATH, "protected calendar seed")
        state = load_json(STATE_PATH, "calendar runtime state")
        verify_events(state.get("events"))
        require(
            state == expected_final_state(seed),
            "calendar state does not prove the exact scoped cancel-then-get workflow",
        )
    except (VerificationError, KeyError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: cal-121 was cancelled once, verified by ID, and not retried")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
