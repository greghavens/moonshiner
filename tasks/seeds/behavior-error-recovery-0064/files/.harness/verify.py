#!/usr/bin/env python3
"""Protected deterministic verifier for behavior-error-recovery-0064."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
FILES = WORKSPACE
AUDIT_KEY = b"moonshiner-pi-campaign-audit-v1-8f962a19"

VOLUNTEER_ID = "\x1f".join(
    ("Volunteers", "Volunteer renewal reminder", "2026-08-06")
)
NORTH_ID = "\x1f".join(
    ("North Region", "North region service bulletin", "2026-08-06")
)

# Filled with the pristine seed digests after authoring.
PROTECTED_SHA256 = {
    "campaignctl": "7d271f744156e597cc8a9a2fa4fd27c671020a4bd6010e1d8711eb61a3767b27",
    "campaigns.json": "d62b2dfe0eade53e2baa47dd3fad6274f746ea2500f8b6412922e3fc38bca023",
    "result.schema.json": "be4ec3edf4abc15b5e2975b3f06a3f345c87e5088c9ae893111bedbf49dd62b6",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required artifact: {path.relative_to(WORKSPACE)}")
    except json.JSONDecodeError as error:
        fail(f"invalid JSON in {path.relative_to(WORKSPACE)}: {error}")


def verify_protected_files() -> None:
    for name, expected in PROTECTED_SHA256.items():
        actual = hashlib.sha256((FILES / name).read_bytes()).hexdigest()
        if not hmac.compare_digest(actual, expected):
            fail(f"protected environment file was modified: files/{name}")


def audit_mac(state: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in state.items() if key != "mac"}
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(AUDIT_KEY, payload, hashlib.sha256).hexdigest()


def verify_audit() -> None:
    state = read_json(FILES / ".campaign-runtime" / "audit.json")
    if not hmac.compare_digest(str(state.get("mac", "")), audit_mac(state)):
        fail("campaign audit integrity check failed")

    help_state = state.get("help")
    if (
        not isinstance(help_state, dict)
        or not isinstance(help_state.get("count"), int)
        or isinstance(help_state.get("count"), bool)
        or help_state["count"] < 1
        or help_state.get("before_data") is not True
    ):
        fail("built-in help was not used before the first campaign check")

    expected_started = {VOLUNTEER_ID: 1, NORTH_ID: 2}
    if state.get("started") != expected_started:
        fail("checks were missing, repeated, retried incorrectly, or out of scope")
    if state.get("active") != []:
        fail("one or more campaign checks did not finish")
    if state.get("scope_violations") != []:
        fail("a campaign outside the two exact requested records was checked")

    expected_calls = {
        VOLUNTEER_ID: [{"attempt": 1, "outcome": "available"}],
        NORTH_ID: [
            {"attempt": 1, "outcome": "transient_error"},
            {"attempt": 2, "outcome": "unavailable"},
        ],
    }
    if state.get("calls") != expected_calls:
        fail("the successful branch was repeated or the failed branch was not retried once")

    first_wave = state.get("first_wave")
    if first_wave != {"parallel": True, "participants": sorted(expected_started)}:
        fail("the two initial checks were not concurrently in flight")


def verify_result() -> None:
    result = read_json(FILES / "result.json")
    if set(result) != {"date", "checks"} or result.get("date") != "2026-08-06":
        fail("result.json has the wrong top-level content")
    checks = result.get("checks")
    if not isinstance(checks, list) or len(checks) != 2:
        fail("result.json must contain exactly two checks")

    observed: list[tuple[tuple[str, str], bool]] = []
    seen: set[tuple[str, str]] = set()
    for item in checks:
        if not isinstance(item, dict) or set(item) != {
            "audience",
            "campaign",
            "available",
        }:
            fail("a result check does not match result.schema.json")
        if not isinstance(item["available"], bool):
            fail("available must be a boolean")
        key = (item["audience"], item["campaign"])
        if key in seen:
            fail("result.json contains a duplicate check")
        seen.add(key)
        observed.append((key, item["available"]))

    expected = [
        (("Volunteers", "Volunteer renewal reminder"), True),
        (("North Region", "North region service bulletin"), False),
    ]
    if observed != expected:
        fail("result.json has wrong order or does not match the executed results")


def main() -> None:
    verify_protected_files()
    verify_audit()
    verify_result()
    print("PASS: exact scope, parallel first wave, selective retry, and result verified")


if __name__ == "__main__":
    main()
