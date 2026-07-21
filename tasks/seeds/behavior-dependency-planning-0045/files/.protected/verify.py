#!/usr/bin/env python3
"""Protected verifier for the case handoff dependency-planning task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "handoff_report.md"
DATABASE = ROOT / ".protected" / "cases.sqlite3"
KEY_FILE = ROOT / ".protected" / "journal.key"
JOURNAL = ROOT / ".pytest_cache" / "case-audit.jsonl"
EXPECTED_DATABASE_SHA256 = "1f717c0a6ec96778af82f06b1be401ee1cb943d603dcc82f71ab734d7715f14b"
REQUESTED = [
    ("Accessibility Caption Delay", "Elm Learning"),
    ("Incorrect Renewal Date", "Frost Dental"),
]


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def check_database() -> None:
    digest = hashlib.sha256(DATABASE.read_bytes()).hexdigest()
    require(digest == EXPECTED_DATABASE_SHA256, "the backing database was modified")
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    try:
        notifications = connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        require(notifications == 0, "the notification log changed")
    finally:
        connection.close()


def read_journal() -> list[dict]:
    require(JOURNAL.is_file(), "no genuine case-tool execution evidence was found")
    key = KEY_FILE.read_bytes()
    entries: list[dict] = []
    for number, line in enumerate(JOURNAL.read_text(encoding="utf-8").splitlines(), 1):
        try:
            envelope = json.loads(line)
            entry = envelope["entry"]
            signature = envelope["signature"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise VerificationError(f"runtime evidence line {number} is invalid") from error
        canonical = json.dumps(
            entry, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        expected = hmac.new(key, canonical, hashlib.sha256).hexdigest()
        require(hmac.compare_digest(signature, expected),
                f"runtime evidence line {number} is not authentic")
        entries.append(entry)
    require(len(entries) == 4,
            "the workflow must contain exactly two searches and two gets")
    return entries


def validate_stage(entries: list[dict], operation: str) -> None:
    require(len(entries) == 2, f"expected exactly two {operation} operations")
    require(all(entry.get("operation") == operation for entry in entries),
            f"the {operation} stage contains another operation")
    require(all(entry.get("exit_code") == 0 for entry in entries),
            f"both {operation} operations must succeed")
    starts = [entry.get("started_ns") for entry in entries]
    ends = [entry.get("ended_ns") for entry in entries]
    require(all(isinstance(value, int) and value > 0 for value in starts + ends),
            f"the {operation} timing evidence is invalid")
    require(all(start < end for start, end in zip(starts, ends)),
            f"the {operation} timing interval is invalid")
    require(max(starts) < min(ends),
            f"the two {operation} processes did not overlap")
    pids = [entry.get("pid") for entry in entries]
    parents = [entry.get("parent_pid") for entry in entries]
    require(all(isinstance(value, int) and value > 0 for value in pids + parents),
            f"the {operation} process evidence is invalid")
    require(len(set(pids)) == 2, f"the {operation} stage did not use two processes")
    require(len(set(parents)) == 1,
            f"the two {operation} processes were not launched by one shell action")


def expected_report(gets_by_id: dict[str, dict], searched_ids: list[str]) -> str:
    records = []
    for (name, location), stable_id in zip(REQUESTED, searched_ids):
        entry = gets_by_id[stable_id]
        response = entry["response"]
        require(response.get("id") == stable_id, "a get returned a different stable ID")
        require(response.get("name") == name and response.get("location") == location,
                "a get returned a different requested record")
        status = response.get("status")
        require(isinstance(status, str) and status,
                "a successful get did not return a status")
        records.append((name, location, stable_id, status))

    first, second = records
    lines = [
        f"- {first[0]} at {first[1]} — ID: {first[2]}; status: {first[3]}",
        f"- {second[0]} at {second[1]} — ID: {second[2]}; status: {second[3]}",
    ]
    if first[3] == second[3]:
        comparison = f"both cases have status {first[3]}."
    else:
        comparison = (
            f"{first[0]} is {first[3]}, while {second[0]} is {second[3]}; "
            "the statuses differ."
        )
    lines.append(f"- Status comparison: {comparison}")
    return "\n".join(lines)


def verify() -> None:
    check_database()
    entries = read_journal()
    require(all(entry.get("operation") in {"search", "get"} for entry in entries),
            "a forbidden case operation was used")
    searches = [entry for entry in entries if entry.get("operation") == "search"]
    gets = [entry for entry in entries if entry.get("operation") == "get"]
    validate_stage(searches, "search")
    validate_stage(gets, "get")
    require(searches[0]["parent_pid"] != gets[0]["parent_pid"],
            "searches and gets must be separate shell-tool actions")
    require(min(entry["started_ns"] for entry in gets)
            >= max(entry["ended_ns"] for entry in searches),
            "a get began before both searches returned")

    searches_by_pair = {}
    for entry in searches:
        arguments = entry.get("arguments")
        require(isinstance(arguments, dict), "a search has invalid arguments")
        require(set(arguments) == {"name", "location"},
                "a search was not an exact name-and-location search")
        pair = (arguments.get("name"), arguments.get("location"))
        require(pair in REQUESTED and pair not in searches_by_pair,
                "searches did not cover each requested branch exactly once")
        response = entry.get("response")
        matches = response.get("matches") if isinstance(response, dict) else None
        require(isinstance(matches, list) and len(matches) == 1,
                "each controlled search must produce exactly one match")
        match = matches[0]
        require(isinstance(match, dict)
                and match.get("name") == pair[0]
                and match.get("location") == pair[1],
                "a search result does not match its requested branch")
        stable_id = match.get("id")
        require(isinstance(stable_id, str) and stable_id,
                "a unique search result has no stable ID")
        searches_by_pair[pair] = stable_id

    searched_ids = [searches_by_pair[pair] for pair in REQUESTED]
    gets_by_id = {}
    for entry in gets:
        arguments = entry.get("arguments")
        require(isinstance(arguments, dict) and set(arguments) == {"id"},
                "a get must use only one returned stable ID")
        stable_id = arguments.get("id")
        require(stable_id in searched_ids and stable_id not in gets_by_id,
                "gets must use each branch's unique searched ID exactly once")
        gets_by_id[stable_id] = entry
    require(set(gets_by_id) == set(searched_ids),
            "both independently resolved branches must be retrieved")

    require(REPORT.is_file(), "handoff_report.md is missing")
    expected = expected_report(gets_by_id, searched_ids)
    actual = REPORT.read_text(encoding="utf-8")
    require(actual == expected or actual == expected + "\n",
            "handoff_report.md does not have the exact evidence-grounded three-bullet format")


def main() -> int:
    try:
        verify()
    except (OSError, sqlite3.Error, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: dependency-aware parallel read-only case audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
