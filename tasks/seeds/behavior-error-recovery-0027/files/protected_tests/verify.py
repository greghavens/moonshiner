#!/usr/bin/env python3
"""Protected checks for the uncertain-commit cancellation workflow."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".inventory"
REASON = "scheduled work is no longer needed"

EXPECTED_RECORDS = [
    {
        "cancellation_reason": REASON,
        "id": "inv-127",
        "location": "Warehouse C",
        "name": "Packing tape",
        "status": "cancelled",
    },
    {
        "cancellation_reason": None,
        "id": "inv-527",
        "location": "Warehouse D",
        "name": "Printer paper",
        "status": "pending",
    },
    {
        "cancellation_reason": "retired stock",
        "id": "inv-627",
        "location": "Warehouse D",
        "name": "Packing tape",
        "status": "closed",
    },
]


def load(name: str):
    return json.loads((STATE_DIR / name).read_text(encoding="utf-8"))


def fail() -> None:
    print(
        "FAIL: the protected uncertain-commit cancellation contract is not satisfied",
        file=sys.stderr,
    )
    raise SystemExit(1)


def exact_request(action: dict, operation: str) -> bool:
    return (
        action.get("operation") == operation
        and action.get("request") == {"id": "inv-127", "reason": REASON}
    )


def main() -> None:
    try:
        if load("records.json") != EXPECTED_RECORDS:
            fail()

        session = load("session.json")
        actions = session.get("actions") if isinstance(session, dict) else None
        if not isinstance(actions, list) or not all(
            isinstance(action, dict) for action in actions
        ):
            fail()

        cancellations = [
            (index, action)
            for index, action in enumerate(actions)
            if action.get("operation") == "cancel"
        ]
        if len(cancellations) != 2:
            fail()
        (first_index, first_cancel), (retry_index, retry) = cancellations

        if not exact_request(first_cancel, "cancel"):
            fail()
        if first_cancel.get("result") != {
            "committed": False,
            "response": "lost_after_submit",
            "status_before": "active",
        }:
            fail()

        expected_verification = {
            "cancellation_reason": None,
            "id": "inv-127",
            "location": "Warehouse C",
            "name": "Packing tape",
            "status": "active",
        }
        verified_active = any(
            action.get("operation") == "get"
            and action.get("request") == {"id": "inv-127"}
            and isinstance(action.get("result"), dict)
            and action["result"].get("record") == expected_verification
            for action in actions[first_index + 1 : retry_index]
        )
        if not verified_active:
            fail()

        if not exact_request(retry, "cancel"):
            fail()
        if retry.get("result") != {
            "id": "inv-127",
            "status_after": "cancelled",
            "status_before": "active",
        }:
            fail()
    except (FileNotFoundError, AttributeError, TypeError, ValueError, json.JSONDecodeError):
        fail()
    print("PASS: uncertain cancellation was verified and retried safely")


if __name__ == "__main__":
    main()
