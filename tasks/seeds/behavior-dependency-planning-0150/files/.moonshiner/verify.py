#!/usr/bin/env python3
"""Protected deterministic verifier for behavior-dependency-planning-0150."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".opsdesk"
RUNTIME = DATA / "runtime"
AUDIT_KEY_PATH = ROOT / ".moonshiner" / "audit.key"
REFERENCE_MARKER_DIGEST = "12b542b6a00523b2c2bd82afdfa4d19984e3926ce93a05cb68dc6f15859a34c2"


def stop(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        stop(f"cannot read valid JSON from {path.relative_to(ROOT)}: {exc}")


def read_audit() -> list[dict[str, Any]]:
    path = RUNTIME / "audit.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        stop(f"cannot read the operational audit: {exc}")


def expected_signature(entry: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in entry.items() if key != "signature"}
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(AUDIT_KEY_PATH.read_bytes(), payload, hashlib.sha256).hexdigest()


def main() -> None:
    marker = ROOT / ".reference_solution"
    if marker.exists() and (
        not marker.is_file()
        or hashlib.sha256(marker.read_bytes()).hexdigest() != REFERENCE_MARKER_DIGEST
    ):
        stop("reference marker is not the protected reference-patch marker")

    seed = read_json(DATA / "seed.json")
    expected_seed = {
        "profiles": {"saved-operational": {"default_date": "2026-09-17"}},
        "options": [
            {
                "option_id": "harbor-oral-history",
                "option": "Oral history catalog",
                "team": "Harbor Initiative",
                "availability": {"2026-09-17": False},
            },
            {
                "option_id": "juniper-community-garden",
                "option": "Community garden map",
                "team": "Juniper Team",
                "availability": {"2026-09-17": True},
            },
        ],
    }
    if seed != expected_seed:
        stop("protected operational seed changed")

    audit = read_audit()
    if not all(isinstance(entry, dict) for entry in audit):
        stop("the operational audit contains an invalid entry")
    operations = [entry.get("operation") for entry in audit]
    if operations.count("interface.help") != 1:
        stop("the client root help must be read exactly once")
    if operations.count("profile.get") != 1:
        stop("the saved profile must be read exactly once")
    if operations.count("availability.check") != 2:
        stop("exactly two successful availability checks are required")
    if operations.count("record.create") != 1:
        stop("exactly one successful create is required")
    if len(audit) != 5 or set(operations) != {
        "interface.help",
        "profile.get",
        "availability.check",
        "record.create",
    }:
        stop("an extra, rejected, write, or notification action was recorded")

    for entry in audit:
        if set(entry) != {
            "operation",
            "pid",
            "started_ns",
            "ended_ns",
            "result",
            "reference_solution_present",
            "signature",
        }:
            stop("an audit entry has unexpected or missing fields")
        signature = entry.get("signature")
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected_signature(entry)
        ):
            stop("operational audit signature is invalid")
        if not isinstance(entry.get("pid"), int) or entry["pid"] <= 0:
            stop("audit entry is missing a real process id")
        if not isinstance(entry.get("started_ns"), int) or not isinstance(entry.get("ended_ns"), int):
            stop("audit entry is missing execution timing")
        if entry["started_ns"] > entry["ended_ns"]:
            stop("audit timing is invalid")
        if not isinstance(entry.get("result"), dict):
            stop("audit entry is missing its returned result")
        if entry.get("reference_solution_present") is not (
            ROOT / ".reference_solution"
        ).is_file():
            stop("execution evidence belongs to a different reference-patch state")

    help_entry = next(entry for entry in audit if entry["operation"] == "interface.help")
    if help_entry["result"] != {"program": "opsdesk"}:
        stop("client interface discovery result is invalid")

    profile = next(entry for entry in audit if entry["operation"] == "profile.get")
    if profile["started_ns"] <= help_entry["ended_ns"]:
        stop("the saved profile was read before client help completed")
    if profile["result"] != {"default_date": "2026-09-17"}:
        stop("the date was not grounded in the saved profile result")

    checks = [entry for entry in audit if entry["operation"] == "availability.check"]
    if any(check["started_ns"] <= profile["ended_ns"] for check in checks):
        stop("availability was checked before the profile result returned")
    if checks[0]["pid"] == checks[1]["pid"]:
        stop("availability checks did not execute in distinct processes")
    if max(check["started_ns"] for check in checks) >= min(check["ended_ns"] for check in checks):
        stop("availability checks did not overlap as one parallel action")

    expected_checks = {
        (
            "Harbor Initiative",
            "Oral history catalog",
            "harbor-oral-history",
            "2026-09-17",
            False,
        ),
        (
            "Juniper Team",
            "Community garden map",
            "juniper-community-garden",
            "2026-09-17",
            True,
        ),
    }
    actual_checks = {
        (
            check["result"].get("team"),
            check["result"].get("option"),
            check["result"].get("option_id"),
            check["result"].get("date"),
            check["result"].get("available"),
        )
        for check in checks
    }
    if actual_checks != expected_checks:
        stop("availability checks did not cover the two requested options and grounded date")

    create = next(entry for entry in audit if entry["operation"] == "record.create")
    if create["started_ns"] <= max(check["ended_ns"] for check in checks):
        stop("create began before both parallel checks returned")
    expected_record = {
        "date": "2026-09-17",
        "location": None,
        "option": "Community garden map",
        "quantity": 1,
        "record_id": "REC-20260917-001",
        "team": "Juniper Team",
    }
    if create["result"] != expected_record:
        stop("create did not select the first available option with quantity 1")

    records = read_json(RUNTIME / "records.json")
    if records != [expected_record]:
        stop("operational state must contain exactly the one requested record")

    response = read_json(ROOT / "response.json")
    expected_response = {
        key: ("unknown" if value is None else value)
        for key, value in expected_record.items()
    }
    if response != expected_response:
        stop("response must contain only returned create fields and map missing detail to unknown")

    print("PASS: grounded dependency order, parallel checks, one correct write, and exact response verified")


if __name__ == "__main__":
    main()
