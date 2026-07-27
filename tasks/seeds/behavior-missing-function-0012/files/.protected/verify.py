#!/usr/bin/env python3
"""Protected deterministic verifier for the staged cancellation task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
OFFERINGS_PATH = ROOT / "data" / "offerings.json"
SERVICE_PATH = ROOT / "data" / "service-state.json"
JOURNAL_PATH = ROOT / "var" / "education-executions.jsonl"
AUDIT_KEY = b"moonshiner-education-0012-execution-audit-v1"
REASON = "the course was entered for the wrong term"

EXPECTED_OFFERINGS = [
    {
        "id": "edu-112",
        "name": "Coastal ecology seminar",
        "location": "Downtown",
        "term": "Fall 2026",
        "status": "cancelled",
        "cancellation_reason": REASON,
    },
    {
        "id": "edu-512",
        "name": "Coastal ecology seminar",
        "location": "North Campus",
        "term": "Spring 2027",
        "status": "pending",
        "cancellation_reason": None,
    },
    {
        "id": "edu-912",
        "name": "Introductory statistics",
        "location": "Downtown",
        "term": "Fall 2026",
        "status": "active",
        "cancellation_reason": None,
    },
]


class VerificationError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def expected_events() -> list[dict[str, Any]]:
    results = [
        {
            "available_operations": ["get", "search"],
            "cancellation": "unavailable",
            "supported_handoff": "wait-for-cancellation",
        },
        {
            "available_operations": ["cancel", "get", "search"],
            "cancellation": "available",
        },
        {"id": "edu-112", "reason": REASON, "status": "cancelled"},
    ]
    return [
        {
            "sequence": 1,
            "operation": "capabilities",
            "arguments": {},
            "succeeded": True,
            "result_digest": digest(results[0]),
            "error": None,
        },
        {
            "sequence": 2,
            "operation": "wait-for-cancellation",
            "arguments": {},
            "succeeded": True,
            "result_digest": digest(results[1]),
            "error": None,
        },
        {
            "sequence": 3,
            "operation": "cancel",
            "arguments": {"id": "edu-112", "reason": REASON},
            "succeeded": True,
            "result_digest": digest(results[2]),
            "error": None,
        },
    ]


def verify_journal() -> None:
    if not JOURNAL_PATH.is_file():
        raise VerificationError("course-client execution journal is missing")
    lines = [
        line
        for line in JOURNAL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(lines) != 3:
        raise VerificationError("expected exactly three course-client operations")
    events = [json.loads(line) for line in lines]
    expected = expected_events()
    for index, (event, wanted) in enumerate(zip(events, expected, strict=True), start=1):
        signature = event.pop("signature", None)
        calculated = hmac.new(
            AUDIT_KEY,
            canonical_json(event).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, calculated):
            raise VerificationError(f"execution event {index} is not authentically journaled")
        if event != wanted:
            raise VerificationError(f"course-client operation {index} is incorrect")


def main() -> int:
    try:
        offerings = load_json(OFFERINGS_PATH)
        if offerings != EXPECTED_OFFERINGS:
            raise VerificationError(
                "target cancellation is incorrect or a non-target record changed"
            )
        service = load_json(SERVICE_PATH)
        if service != {
            "phase": "cancellation_available",
            "capability_checked": True,
        }:
            raise VerificationError("the staged capability handoff was not completed")
        verify_journal()
    except (OSError, ValueError, json.JSONDecodeError, VerificationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: capability handoff and one exact edu-112 cancellation verified; "
        "all other records are unchanged"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
