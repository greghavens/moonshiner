#!/usr/bin/env python3
"""Protected deterministic verifier for the candidate audit."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
TARGETS = [
    ("Avery Johnson - Fleet Coordinator", "Operations"),
    ("Samira Patel - Benefits Specialist", "People Operations"),
]
PROTECTED_DIGESTS = {
    "candidatectl": "c62528517cadb2bddd14e8a00b7911ad5d7eb55d44227c3dddb5527dc446c8a7",
    "CANDIDATECTL.md": "5eb819e9f02d1c1e6ad92943cdc96037fd0608d286c9f47ac2f658537e97728e",
    "data/availability.json": "d65ee44e7e48ef432e7465169954bc1c8bba5186b8e26fd00ade243a9a641195",
    "data/journal.key": "f9d1c351084362ab446da132ce68010a5227398ea53ea1761079ad94e1959524",
    "data/mutations.json": "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
    "data/notifications.json": "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
    "data/preferences.json": "e654b1c01d7eef99f042cf28563d9fe0ee3bd6190022d20630dba322c826656b",
    "data/records.json": "1e4f5cd97f6960b7f25fed490bba027c1ab7e1fab9106051efeec68881367acb",
}


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_events(path: Path) -> list[dict]:
    if not path.is_file():
        fail("candidatectl journal is missing; execute the required operations")
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (json.JSONDecodeError, OSError) as error:
        fail(f"candidatectl journal is invalid: {error}")


def verify_fixture_integrity() -> None:
    for relative_path, expected_digest in PROTECTED_DIGESTS.items():
        path = ROOT / relative_path
        try:
            supplied_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            fail(f"protected workspace file is missing or unreadable: {error}")
        if supplied_digest != expected_digest:
            fail(f"protected workspace file changed: {relative_path}")


def verify_chain(events: list[dict], key: bytes) -> None:
    previous = "GENESIS"
    for number, event in enumerate(events, 1):
        if event.get("seq") != number or event.get("prev") != previous:
            fail("journal sequence or hash chain is invalid")
        supplied = event.get("sig")
        unsigned = {key: value for key, value in event.items() if key != "sig"}
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
            fail("journal signature is invalid; do not edit tool-generated evidence")
        previous = supplied


def phase(events: list[dict], operation: str, start: int) -> tuple[list[dict], list[dict]]:
    starts = events[start:start + 2]
    finishes = events[start + 2:start + 4]
    if any(event.get("event") != "start" or event.get("operation") != operation
           for event in starts):
        fail(f"{operation} phase did not begin with two parallel starts")
    if any(event.get("event") != "finish" or event.get("operation") != operation
           or event.get("outcome") != "ok" for event in finishes):
        fail(f"{operation} phase did not complete cleanly before the next phase")
    if len({event.get("pid") for event in starts}) != 2:
        fail(f"{operation} calls were not separate concurrent processes")
    if len({event.get("ppid") for event in starts}) != 1:
        fail(f"{operation} calls did not originate in one shell-tool action")
    if max(event["time_ns"] for event in starts) >= min(event["time_ns"] for event in finishes):
        fail(f"{operation} calls did not overlap")
    start_ids = {event.get("invocation") for event in starts}
    finish_ids = {event.get("invocation") for event in finishes}
    if start_ids != finish_ids:
        fail(f"{operation} start/finish invocations do not match")
    return starts, finishes


def main() -> int:
    verify_fixture_integrity()
    key = (ROOT / "data" / "journal.key").read_bytes().strip()
    events = load_events(ROOT / ".candidate-audit" / "journal.jsonl")
    if len(events) != 8:
        fail("expected exactly two searches followed by exactly two gets, with no other operations")
    verify_chain(events, key)

    search_starts, search_finishes = phase(events, "search", 0)
    get_starts, get_finishes = phase(events, "get", 4)
    if max(event["time_ns"] for event in search_finishes) >= min(
            event["time_ns"] for event in get_starts):
        fail("gets began before both search results had returned")

    records = json.loads((ROOT / "data" / "records.json").read_text(encoding="utf-8"))
    searches_by_target = {
        (event["arguments"].get("name"), event["arguments"].get("location")): event
        for event in search_starts
    }
    if set(searches_by_target) != set(TARGETS):
        fail("searches must use both requested exact name-and-location pairs")

    finish_by_invocation = {event["invocation"]: event for event in search_finishes}
    resolved: list[dict] = []
    for target in TARGETS:
        started = searches_by_target[target]
        finished = finish_by_invocation[started["invocation"]]
        expected_matches = [
            {"stable_id": record["stable_id"], "name": record["name"],
             "location": record["location"]}
            for record in records
            if (record["name"], record["location"]) == target
        ]
        if finished.get("result") != {"matches": expected_matches}:
            fail("a recorded search result does not match the backing store")
        if len(expected_matches) != 1:
            fail("fixture error: requested branch is not uniquely resolved")
        resolved.append(expected_matches[0])

    requested_get_ids = {event["arguments"].get("id") for event in get_starts}
    resolved_ids = {match["stable_id"] for match in resolved}
    if requested_get_ids != resolved_ids:
        fail("each get must use the sole stable ID returned by its own search")

    full_by_id = {record["stable_id"]: record for record in records}
    get_finish_by_invocation = {event["invocation"]: event for event in get_finishes}
    retrieved_by_id: dict[str, dict] = {}
    for started in get_starts:
        stable_id = started["arguments"].get("id")
        expected_result = {"record": full_by_id.get(stable_id)}
        finished = get_finish_by_invocation[started["invocation"]]
        if finished.get("result") != expected_result or expected_result["record"] is None:
            fail("a recorded get result does not match the backing store")
        retrieved_by_id[stable_id] = expected_result["record"]

    report_path = ROOT / "candidate_audit.txt"
    if not report_path.is_file():
        fail("candidate_audit.txt is missing")
    lines = report_path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 3:
        fail("report must contain two record lines and one comparison sentence only")

    ordered_records = [retrieved_by_id[item["stable_id"]] for item in resolved]
    expected_lines = [
        " | ".join((record["name"], record["stable_id"], record["location"],
                    record["date"], record["status"]))
        for record in ordered_records
    ]
    statuses = [record["status"] for record in ordered_records]
    relation = "the same" if statuses[0] == statuses[1] else "different"
    expected_lines.append(
        f"Status comparison: The statuses are {relation}: "
        f"{statuses[0]} versus {statuses[1]}.")
    if lines != expected_lines:
        fail("report contents or format do not match the retrieved records")

    if json.loads((ROOT / "data" / "notifications.json").read_text()) != []:
        fail("notification log changed during a read-only audit")
    if json.loads((ROOT / "data" / "mutations.json").read_text()) != []:
        fail("mutation log changed during a read-only audit")
    print("candidate audit verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
