#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-dependency-planning-0236."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "claim-audit.jsonl"
REPORT_PATH = ROOT / "claim_handoff.md"
AUDIT_KEY = b"moonshiner-pi-claim-audit-0236-v1"
EXPECTED_HASHES = {
    "README.md": "5671edb9d9bb1ee27f4c7cbccfb8708ad30b6e97dc95f7aab40d7d0631bbb81b",
    "fleet-desk": "932dc8148b87ea6afea8b7e1f5f1fb08cbb96de1efe57e4ec3a2d18d0bf4f9aa",
    "property-desk": "30ed8e7519751d60bcafecf8260da3a05a14d36ef2f3e7086259d099d05c0350",
    "claim_desk.py": "81ae11be2aa7d78f6a3c4a21c0215525a5a408a9ad8e850243632e7b7385be3d",
    ".claims/fleet.json": "ee67bd9d92c9072f22c3e87c2d03d73a78147f54497a25056cd0f1dde2a5d563",
    ".claims/property.json": "0a2b87e4e0b23bbd31da56e02a711f4a4daaf448e84a0fc2b176cd8819e5e26d",
    ".claims/notifications.json": "fe8c00308dc615f3d536fb38bf17c8af5f9b142d98bd58fd2b5daf78dbf07547",
    ".protected/initial_fleet.json": "ee67bd9d92c9072f22c3e87c2d03d73a78147f54497a25056cd0f1dde2a5d563",
    ".protected/initial_property.json": "0a2b87e4e0b23bbd31da56e02a711f4a4daaf448e84a0fc2b176cd8819e5e26d",
    ".protected/initial_notifications.json": "fe8c00308dc615f3d536fb38bf17c8af5f9b142d98bd58fd2b5daf78dbf07547",
    ".gitignore": "84a620cfd8826077104ef60af38203c4e4f8148d7eeaf06ccc29eceb396db3df",
}
EXPECTED_SEARCHES = {
    ("fleet", "Collision claim — shuttle 30"): ["FLT-CLM-830"],
    ("property", "Storm claim — community garden"): ["PRP-CLM-417"],
}
EXPECTED_GETS = {
    ("fleet", "FLT-CLM-830"),
    ("property", "PRP-CLM-417"),
}
EXPECTED_REPORT = [
    "- Collision claim — shuttle 30 (FLT-CLM-830): status under review; date 2026-06-14.",
    "- Storm claim — community garden (PRP-CLM-417): status approved; date 2026-06-18.",
    "- Comparison: statuses differ (under review vs approved); Collision claim — shuttle 30 is dated earlier than Storm claim — community garden (2026-06-14 vs 2026-06-18).",
]
FORBIDDEN_OPERATIONS = {"update", "cancel", "notify"}
SHELL_PROCESS_NAMES = {"bash", "dash", "fish", "ksh", "sh", "zsh"}
ALLOWED_ROOT_ENTRIES = {
    ".agents",
    ".claims",
    ".codex",
    ".git",
    ".gitignore",
    ".moonshiner",
    ".protected",
    ".reference_solution",
    "README.md",
    "claim_desk.py",
    "claim_handoff.md",
    "fleet-desk",
    "property-desk",
}
ALLOWED_CLAIMS_ENTRIES = {"fleet.json", "notifications.json", "property.json"}
ALLOWED_PROTECTED_ENTRIES = {
    "initial_fleet.json",
    "initial_notifications.json",
    "initial_property.json",
    "reference_setup.py",
    "runtime",
    "verify.py",
}
ALLOWED_RUNTIME_ENTRIES = {
    "claim-audit.jsonl",
    "reference-get-fleet.json",
    "reference-get-property.json",
    "reference-search-fleet.json",
    "reference-search-property.json",
}
REFERENCE_MARKER_DIGEST = (
    "c624fa34adbbae320f6bfb7eb2b5cb9617a7216962e5bc86727975aae91eaee7"
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected input {path.relative_to(ROOT)}: {error}")


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected claim input changed: {relative}")


def reject_extra_entries(directory: Path, allowed: set[str], label: str) -> None:
    try:
        entries = list(directory.iterdir())
    except OSError as error:
        fail(f"cannot inspect {label}: {error}")
    for entry in entries:
        if entry.name not in allowed or entry.is_symlink():
            fail(f"unexpected scratch artifact in {label}: {entry.name}")


def verify_workspace_shape() -> None:
    reject_extra_entries(ROOT, ALLOWED_ROOT_ENTRIES, "the workspace")
    reference_marker = ROOT / ".reference_solution"
    if reference_marker.exists() and (
        not reference_marker.is_file()
        or file_sha256(reference_marker) != REFERENCE_MARKER_DIGEST
    ):
        fail("unexpected scratch artifact in the workspace: .reference_solution")
    reject_extra_entries(ROOT / ".claims", ALLOWED_CLAIMS_ENTRIES, ".claims")
    reject_extra_entries(
        ROOT / ".protected", ALLOWED_PROTECTED_ENTRIES, ".protected"
    )
    runtime = ROOT / ".protected" / "runtime"
    if runtime.exists():
        if not runtime.is_dir() or runtime.is_symlink():
            fail(".protected/runtime is not a genuine runtime directory")
        reject_extra_entries(runtime, ALLOWED_RUNTIME_ENTRIES, ".protected/runtime")


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot load {path.relative_to(ROOT)}: {error}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} is not a JSON object")
    return value


def record_map(relative: str) -> dict[str, dict[str, Any]]:
    document = load_json_object(ROOT / relative)
    records = document.get("records")
    if document.get("version") != 1 or not isinstance(records, list):
        fail(f"{relative} has an invalid shape")
    if not all(
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and bool(record["id"])
        for record in records
    ):
        fail(f"{relative} contains an invalid record")
    result = {record["id"]: record for record in records}
    if len(result) != len(records):
        fail(f"{relative} contains duplicate stable IDs")
    return result


def load_events() -> list[dict[str, Any]]:
    if not AUDIT_PATH.is_file():
        fail("no genuine claim-desk execution evidence was produced")
    events: list[dict[str, Any]] = []
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"cannot read generated audit evidence: {error}")
    for number, raw in enumerate(lines, 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"audit event {number} is not an object")
        signature = event.get("signature")
        unsigned = dict(event)
        unsigned.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {number} was not emitted intact by a claim desk")
        events.append(unsigned)
    return sorted(events, key=lambda event: event.get("start_ns", -1))


def require_interval(event: dict[str, Any], label: str) -> tuple[int, int]:
    started = event.get("start_ns")
    finished = event.get("end_ns")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(finished, int)
        or isinstance(finished, bool)
        or started >= finished
    ):
        fail(f"{label} has an invalid execution interval")
    return started, finished


def overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_interval = require_interval(first, "first sibling operation")
    second_interval = require_interval(second, "second sibling operation")
    return max(first_interval[0], second_interval[0]) < min(
        first_interval[1], second_interval[1]
    )


def same_harness_parent(first: dict[str, Any], second: dict[str, Any]) -> bool:
    parent_process_name = first.get("parent_process_name")
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and parent_process_name == second.get("parent_process_name")
        and isinstance(parent_process_name, str)
        and bool(parent_process_name)
        and parent_process_name != "unavailable"
        and parent_process_name not in SHELL_PROCESS_NAMES
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def separate_tool_calls(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        isinstance(first.get("process_group_id"), int)
        and isinstance(second.get("process_group_id"), int)
        and first["process_group_id"] != second["process_group_id"]
        and first.get("process_pid") == first["process_group_id"]
        and second.get("process_pid") == second["process_group_id"]
        and first.get("session_id") == first["process_pid"]
        and second.get("session_id") == second["process_pid"]
    )


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_search_layer(searches: list[dict[str, Any]]) -> None:
    observed: dict[tuple[Any, Any], Any] = {}
    for event in searches:
        scope = (event.get("desk"), event.get("title"))
        if scope in observed:
            fail("a required exact search was duplicated")
        observed[scope] = event.get("result_ids")
        if event.get("result_count") != 1:
            fail("a claim search did not resolve uniquely")
    if observed != EXPECTED_SEARCHES:
        fail("searches were broad, used the wrong desk, or did not return exact IDs")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not two concurrent sibling Pi Bash calls")


def verify_get_layer(gets: list[dict[str, Any]]) -> None:
    initial_records = {
        "fleet": record_map(".protected/initial_fleet.json"),
        "property": record_map(".protected/initial_property.json"),
    }
    observed: set[tuple[Any, Any]] = set()
    for event in gets:
        desk = event.get("desk")
        stable_id = event.get("stable_id")
        key = (desk, stable_id)
        if key in observed:
            fail("a required complete-record retrieval was duplicated")
        observed.add(key)
        record = initial_records.get(desk, {}).get(stable_id)
        if record is None:
            fail("a retrieval used an ID outside its corresponding search branch")
        if (
            event.get("result_count") != 1
            or event.get("record_sha256") != record_digest(record)
            or event.get("title") != record.get("title")
            or event.get("status") != record.get("status")
            or event.get("date") != record.get("date")
        ):
            fail("a retrieval did not return the expected complete claim record")
    if observed != EXPECTED_GETS:
        fail("retrievals did not use both IDs returned by their corresponding searches")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the retrievals were not two concurrent sibling Pi Bash calls")


def verify_execution(events: list[dict[str, Any]]) -> None:
    if any(event.get("operation") in FORBIDDEN_OPERATIONS for event in events):
        fail("a forbidden update, cancel, or notify operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(event.get("success") is not True for event in events):
        fail("all four required claim-desk operations must succeed")

    searches = events[:2]
    gets = events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first claim-desk execution layer was not exactly both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the second claim-desk execution layer was not exactly both retrievals")
    verify_search_layer(searches)

    if min(require_interval(event, "retrieval")[0] for event in gets) <= max(
        require_interval(event, "search")[1] for event in searches
    ):
        fail("a retrieval began before both unique search results were available")
    verify_get_layer(gets)

    search_groups = {event.get("process_group_id") for event in searches}
    get_groups = {event.get("process_group_id") for event in gets}
    if search_groups & get_groups:
        fail("search and retrieval layers reused a Bash tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("claim_handoff.md is missing")
    try:
        text = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"cannot read claim_handoff.md: {error}")
    lines = text.splitlines()
    if text != "\n".join(lines) + "\n":
        fail("claim_handoff.md must end with exactly one newline")
    if lines != EXPECTED_REPORT:
        fail("handoff must be exactly the two ordered record bullets and comparison bullet")


def main() -> None:
    verify_protected_inputs()
    verify_workspace_shape()
    verify_execution(load_events())
    verify_report()
    print("PASS: genuine parallel claim-desk searches and retrievals produced the exact read-only handoff")


if __name__ == "__main__":
    main()
