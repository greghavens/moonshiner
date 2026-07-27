#!/usr/bin/env python3
"""Protected deterministic verification for the Pi uncertain-commit task."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".permit-state.json"
SEED_PATH = ROOT / "data" / "seed.json"
TARGET_ID = "pub-119"
TARGET_NAME = "Pet License"
REASON = "created for the wrong operating period."


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path.name} must contain a JSON object")
    return value


def by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    applications = state.get("applications")
    require(isinstance(applications, list), "applications must be a list")
    result = {
        item["id"]: item
        for item in applications
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    require(len(result) == len(applications), "application IDs must be present and unique")
    return result


def request_fingerprint() -> str:
    canonical = json.dumps(
        {"application_id": TARGET_ID, "reason": REASON},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_state(seed: dict[str, Any], current: dict[str, Any]) -> None:
    require(seed.get("schema_version") == 1, "protected seed schema is invalid")
    require(current.get("schema_version") == 1, "service schema changed")
    initial = by_id(seed)
    final = by_id(current)
    require(set(final) == set(initial), "the application collection changed")
    require(
        initial[TARGET_ID].get("name") == TARGET_NAME,
        "protected target identity is invalid",
    )

    expected_target = dict(initial[TARGET_ID])
    expected_target["status"] = "cancelled"
    expected_target["cancellation_reason"] = REASON
    require(final[TARGET_ID] == expected_target, "pub-119 was not cancelled exactly as requested")
    for application_id, initial_record in initial.items():
        if application_id != TARGET_ID:
            require(
                final[application_id] == initial_record,
                "an out-of-scope application was altered",
            )

    require(
        current.get("notifications") == seed.get("notifications") == [],
        "a contact notification was created",
    )
    expected_faults = json.loads(json.dumps(seed["faults"]))
    expected_faults["cancel_timeout_after_commit"][TARGET_ID] = 0
    require(
        current.get("faults") == expected_faults,
        "the one-shot timeout state does not match a reached-service attempt",
    )


def verify_journal(current: dict[str, Any]) -> None:
    expected = [
        {
            "after_status": "cancelled",
            "application_id": TARGET_ID,
            "before_status": "active",
            "operation": "cancel",
            "outcome": "timeout_after_commit",
            "reason": REASON,
            "request_fingerprint": request_fingerprint(),
            "sequence": 1,
        },
        {
            "application_id": TARGET_ID,
            "observed_status": "cancelled",
            "operation": "get",
            "outcome": "returned",
            "sequence": 2,
        },
    ]
    require(
        current.get("audit") == expected,
        "expected exactly one timed-out cancellation followed by one direct get and no retry",
    )


def main() -> int:
    try:
        require(STATE_PATH.is_file() and not STATE_PATH.is_symlink(), "permit service state is missing")
        seed = load_json(SEED_PATH)
        current = load_json(STATE_PATH)
        verify_state(seed, current)
        verify_journal(current)
    except (VerificationError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: uncertain commit resolved by direct get; scoped cancellation and no-retry branch verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
