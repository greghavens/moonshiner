#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi facilities workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "facility-audit.jsonl"
REPORT_PATH = ROOT / "operations_check.md"
AUDIT_KEY = b"moonshiner-pi-facility-audit-0217-v1"

EXPECTED_HASHES = {
    "facility-registry": "3ae3d99ba0c6347cfebe6602ce67b07fdc3aab77cf92f3a4abab787e1ed44edf",
    ".facilities/records.json": "35e395dff0bda8d4f0933258bb72cf5fe6a5e717bb1ec5f3995f201ebf4ee8fc",
    "README.md": "bda7bace476d954624649336763308ab54e8e98435d395d1d4cd713ce456b0c4",
    ".gitignore": "fc2976dd6b53062a23eeffaf4943364ff37cba90ddf293b81f82bd481f30f58c",
}
EXPECTED_SEARCHES = {
    ("Loading dock door inspection", "Warehouse"),
    ("Quiet room signage installation", "Main Office"),
}
EXPECTED_IDS = {"fac-317", "fac-717"}
EXPECTED_REPORT = (
    "Loading dock door inspection in Warehouse has status open and date 2026-09-24.\n"
    "Quiet room signage installation in Main Office has status materials-ordered "
    "and date 2026-09-25.\n"
    "The returned statuses differ, and the returned dates differ.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_WORKSPACE_PATHS = {
    ".facilities",
    ".facilities/records.json",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/facility-audit.jsonl",
    ".harness/verify.py",
    ".gitignore",
    "README.md",
    "facility-registry",
    "operations_check.md",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_workspace_layout() -> None:
    paths = []
    for top_level in ROOT.iterdir():
        if top_level.name == ".git":
            continue
        paths.append(top_level)
        if top_level.is_dir():
            paths.extend(top_level.rglob("*"))
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        if relative not in ALLOWED_WORKSPACE_PATHS:
            fail(f"unexpected workspace artifact: {relative}")


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected facilities input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine facility-registry execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by the registry")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict, second: dict) -> bool:
    return (
        first["parent_pid"] == second["parent_pid"]
        and first["parent_start_ticks"] == second["parent_start_ticks"]
        and first["parent_start_ticks"] != "unavailable"
    )


def separate_tool_calls(first: dict, second: dict) -> bool:
    return (
        isinstance(first.get("process_group_id"), int)
        and isinstance(second.get("process_group_id"), int)
        and first["process_group_id"] != second["process_group_id"]
        and first.get("process_pid") == first["process_group_id"]
        and second.get("process_pid") == second["process_group_id"]
        and first.get("session_id") == first["process_pid"]
        and second.get("session_id") == second["process_pid"]
    )


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an operation outside the read-only facilities check was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("all required facility-registry operations must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the later registry action must contain only both gets")

    observed_searches = {
        (event.get("query"), event.get("location")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact query-and-location searches were not executed")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both request searches must resolve to exactly one stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        or not event["result_ids"][0]
        for event in searches
    ):
        fail("a search did not return one auditable stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not concurrent sibling Pi Bash tool calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a get began before both search results were available")
    returned_ids = {event["result_ids"][0] for event in searches}
    if returned_ids != EXPECTED_IDS:
        fail("the exact searches did not return the expected stable records")
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("gets did not use exactly the stable IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete facilities request was not retrieved")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the gets were not concurrent sibling Pi Bash tool calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in gets
    }:
        fail("search and get stages reused a Bash tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("operations_check.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("operations_check.md does not have the exact grounded comparison")


def main() -> None:
    verify_workspace_layout()
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded facilities comparison and genuine Pi registry execution "
        "satisfy all constraints"
    )


if __name__ == "__main__":
    main()
