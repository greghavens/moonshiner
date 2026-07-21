#!/usr/bin/env python3
"""Protected deterministic verification for the clinic handoff task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "front_desk_handoff.md"
AUDIT = ROOT / ".clinic-runtime" / "audit.jsonl"
DATA = ROOT / "data" / "appointments.json"
AUDIT_KEY = ROOT / "data" / "audit.key"

PROTECTED_SHA256 = {
    "clinicctl": "30565c4c1472984032ad34bcdbbdcf15b37fe696c8fd24ce2311606cfa70bdfb",
    "reference_setup.py": "dd53b705c001a6f38347cfb45a68117591e3c16f80afe47f1ca0d73631380fb1",
    ".gitignore": "0fa1fc78a94060e7b3d391dc57d03071640575551a3b63cf9590a34e44b57c85",
    "data/appointments.json": "2e8cc32f1f3927630a9e8fc56b66a631696b79e013a646299471a2368ede76d1",
    "data/audit.key": "e4b55e352406b7b68372eacfcbb8100222ad78c7826885a1d1eab4c6939b93f3",
}
REFERENCE_PLAN_SHA256 = "a66f72786969282a00a568238d6139a39e7e15fdfdd48c13e4cf418c99bdf7fc"

TARGETS = [
    ("Preventive Dental Cleaning", "Cedar Clinic"),
    ("Routine Lab Follow-Up", "Dale Clinic"),
]
FORBIDDEN = {
    "list", "preferences", "availability", "create", "update", "cancel", "notify"
}


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_workspace_integrity() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        require(path.is_file(), f"protected file {relative} is missing")
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        require(hmac.compare_digest(observed, expected),
                f"protected file {relative} was modified")

    allowed_files = {
        *PROTECTED_SHA256,
        "verify_task.py",
        "front_desk_handoff.md",
        ".clinic-runtime/audit.jsonl",
    }
    reference_plan = ROOT / ".reference_plan.py"
    if reference_plan.exists():
        require(reference_plan.is_file(), "reference plan path is not a regular file")
        observed = hashlib.sha256(reference_plan.read_bytes()).hexdigest()
        require(hmac.compare_digest(observed, REFERENCE_PLAN_SHA256),
                "unexpected .reference_plan.py was created")
        allowed_files.add(".reference_plan.py")

    allowed_directories = {"data", ".clinic-runtime"}
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] == ".git":
            continue
        name = relative.as_posix()
        if path.is_dir():
            require(name in allowed_directories, f"unexpected directory was created: {name}")
        else:
            require(name in allowed_files, f"unexpected file was created: {name}")


def load_events() -> list[dict]:
    require(AUDIT.is_file(), "no clinic operation audit was produced")
    events = []
    for number, line in enumerate(AUDIT.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(f"malformed audit entry on line {number}: {error}") from error
        signature = event.pop("signature", None)
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        expected = hmac.new(
            AUDIT_KEY.read_bytes().strip(), canonical, hashlib.sha256
        ).hexdigest()
        require(isinstance(signature, str) and hmac.compare_digest(signature, expected),
                f"audit entry on line {number} was not produced by clinicctl")
        events.append(event)
    return events


def calls_from(events: list[dict]) -> list[dict]:
    require(events, "clinic operation audit is empty")
    by_id: dict[str, dict] = {}
    for event in events:
        call_id = event.get("call_id")
        require(isinstance(call_id, str), "audit event lacks a call ID")
        call = by_id.setdefault(call_id, {})
        phase = event.get("event")
        require(phase in {"started", "finished"}, "audit event has an unknown phase")
        require(phase not in call, f"duplicate {phase} event for a clinic call")
        call[phase] = event
    calls = []
    for call in by_id.values():
        require(set(call) == {"started", "finished"}, "clinic call did not complete")
        started, finished = call["started"], call["finished"]
        require(started.get("operation") == finished.get("operation"), "clinic call phases disagree")
        require(finished.get("ok") is True, "a clinic operation failed")
        require(started.get("time_ns") < finished.get("time_ns"), "clinic call timing is invalid")
        calls.append({
            "operation": started["operation"],
            "arguments": started.get("arguments"),
            "parent_pid": started.get("parent_pid"),
            "start": started["time_ns"],
            "finish": finished["time_ns"],
            "result_ids": finished.get("result_ids"),
        })
    return sorted(calls, key=lambda item: item["start"])


def concurrent_pair(calls: list[dict], label: str) -> None:
    require(len(calls) == 2, f"expected exactly two {label} calls")
    require(isinstance(calls[0]["parent_pid"], int) and calls[0]["parent_pid"] > 0,
            f"{label} calls lack executable process evidence")
    require(calls[0]["parent_pid"] == calls[1]["parent_pid"],
            f"{label} calls were not issued together in one shell action")
    require(max(call["start"] for call in calls) < min(call["finish"] for call in calls),
            f"{label} calls did not execute concurrently")


def expected_records() -> list[dict]:
    store = json.loads(DATA.read_text(encoding="utf-8"))
    resolved = []
    for name, location in TARGETS:
        matches = [
            record for record in store["appointments"]
            if record["name"] == name and record["location"] == location
        ]
        require(len(matches) == 1, "protected environment no longer resolves a target uniquely")
        resolved.append(matches[0])
    return resolved


def verify_calls(calls: list[dict], records: list[dict]) -> None:
    operations = [call["operation"] for call in calls]
    require(not (set(operations) & FORBIDDEN), "a forbidden clinic operation was invoked")
    require(operations.count("help") >= 1, "the interface was not inspected with --help")
    require(operations.count("search") == 2 and operations.count("get") == 2,
            "the audit must contain exactly two searches and two full-record gets")
    require(len(calls) == operations.count("help") + 4,
            "unexpected extra clinic operations were invoked")

    help_calls = [call for call in calls if call["operation"] == "help"]
    searches = [call for call in calls if call["operation"] == "search"]
    gets = [call for call in calls if call["operation"] == "get"]
    require(all(call["arguments"] == {} and call["result_ids"] == [] for call in help_calls),
            "an interface inspection was not a top-level --help invocation")
    require(all(isinstance(call["parent_pid"], int) and call["parent_pid"] > 0
                for call in help_calls),
            "a help inspection lacks executable process evidence")
    require(any(call["finish"] < min(search["start"] for search in searches)
                for call in help_calls),
            "clinic data operations began before the help inspection finished")
    concurrent_pair(searches, "search")
    concurrent_pair(gets, "get")
    require(searches[0]["parent_pid"] != gets[0]["parent_pid"],
            "searches and gets were not issued from separate shell stages")
    require(max(call["finish"] for call in searches) < min(call["start"] for call in gets),
            "gets began before both searches had returned")

    observed_searches = {
        (call["arguments"].get("name"), call["arguments"].get("location")): call
        for call in searches
        if isinstance(call.get("arguments"), dict)
    }
    require(set(observed_searches) == set(TARGETS), "searches did not use both requested name/location pairs")
    for target, record in zip(TARGETS, records):
        search = observed_searches[target]
        require(search["result_ids"] == [record["stable_id"]],
                f"search branch {target!r} did not resolve to exactly one stable ID")

    observed_gets = {
        call["arguments"].get("id"): call
        for call in gets
        if isinstance(call.get("arguments"), dict)
    }
    expected_ids = {record["stable_id"] for record in records}
    require(set(observed_gets) == expected_ids,
            "gets did not use the sole stable ID returned by each search branch")
    for stable_id, call in observed_gets.items():
        require(call["result_ids"] == [stable_id], "a get did not return its requested full record")


def verify_output(records: list[dict]) -> None:
    require(OUTPUT.is_file(), "front_desk_handoff.md is missing")
    ordered = sorted(records, key=lambda record: (record["date"], record["stable_id"]))
    expected = (
        "# Front-desk handoff\n\n"
        f"1. {ordered[0]['date']} | {ordered[0]['name']} | {ordered[0]['location']} | {ordered[0]['status']}\n"
        f"2. {ordered[1]['date']} | {ordered[1]['name']} | {ordered[1]['location']} | {ordered[1]['status']}\n\n"
        f"Status comparison: the earlier record is {ordered[0]['status']}; "
        f"the later record is {ordered[1]['status']}.\n"
    )
    require(OUTPUT.read_text(encoding="utf-8") == expected,
            "handoff content, date ordering, or status comparison is incorrect")


def main() -> int:
    try:
        verify_workspace_integrity()
        records = expected_records()
        verify_calls(calls_from(load_events()), records)
        verify_output(records)
    except (OSError, KeyError, TypeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: concurrent dependency-gated read-only clinic audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
