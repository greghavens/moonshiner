#!/usr/bin/env python3
"""Protected verifier for the shipment dependency-planning task."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "exception-board.txt"
EXECUTABLE = ROOT / "bin" / "shipmentctl"
DATABASE = ROOT / ".protected" / "shipments.sqlite3"
KEY_FILE = ROOT / ".protected" / "journal.key"
JOURNAL = ROOT / ".pi-runtime" / "shipment-audit.jsonl"
EXPECTED_EXECUTABLE_SHA256 = "8bdbf091dd7beacf76162fd15e9663dfe53f0e73a5a7e32b8ebd7fa11a8ba829"
EXPECTED_DATABASE_SHA256 = "428c0c1874667050d22e91ece7733fc66a9ce299af6ed99bb6d2e838f5161cab"
EXPECTED_KEY_SHA256 = "0aba39127fb169b147db31e5027d68cd4af4d6314386276315132f91c5b1a6cf"
REQUESTED = [
    ("Northstar Equipment Pallet", "Minneapolis Hub"),
    ("Lakeside Book Carton", "Madison Hub"),
]


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_protected_inputs() -> None:
    require(
        sha256(EXECUTABLE) == EXPECTED_EXECUTABLE_SHA256,
        "the shipment executable was modified",
    )
    require(
        sha256(DATABASE) == EXPECTED_DATABASE_SHA256,
        "the backing database was modified",
    )
    require(sha256(KEY_FILE) == EXPECTED_KEY_SHA256, "the signing material changed")
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as connection:
        notifications = connection.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        mutations = connection.execute(
            "SELECT COUNT(*) FROM mutation_log"
        ).fetchone()[0]
    require(notifications == 0, "the notification log changed")
    require(mutations == 0, "the mutation log changed")


def read_journal() -> list[dict]:
    require(JOURNAL.is_file(), "no genuine shipment-tool execution evidence was found")
    key = KEY_FILE.read_bytes()
    entries: list[dict] = []
    for number, line in enumerate(JOURNAL.read_text(encoding="utf-8").splitlines(), 1):
        try:
            envelope = json.loads(line)
            entry = envelope["entry"]
            signature = envelope["signature"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise VerificationError(
                f"runtime evidence line {number} is invalid"
            ) from error
        canonical = json.dumps(
            entry, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        expected = hmac.new(key, canonical, hashlib.sha256).hexdigest()
        require(
            hmac.compare_digest(signature, expected),
            f"runtime evidence line {number} is not authentic",
        )
        entries.append(entry)
    require(
        len(entries) == 4,
        "the workflow must contain exactly two searches and two gets",
    )
    return entries


def validate_parallel_stage(entries: list[dict], operation: str) -> None:
    require(len(entries) == 2, f"expected exactly two {operation} operations")
    require(
        all(entry.get("operation") == operation for entry in entries),
        f"the {operation} stage contains another operation",
    )
    require(
        all(entry.get("exit_code") == 0 for entry in entries),
        f"both {operation} operations must succeed",
    )
    starts = [entry.get("started_ns") for entry in entries]
    ends = [entry.get("ended_ns") for entry in entries]
    require(
        all(isinstance(value, int) and value > 0 for value in starts + ends),
        f"the {operation} timing evidence is invalid",
    )
    require(
        all(start < end for start, end in zip(starts, ends)),
        f"the {operation} timing interval is invalid",
    )
    require(
        max(starts) < min(ends),
        f"the two {operation} processes did not overlap",
    )
    pids = [entry.get("pid") for entry in entries]
    parents = [entry.get("parent_pid") for entry in entries]
    require(
        all(isinstance(value, int) and value > 0 for value in pids + parents),
        f"the {operation} process evidence is invalid",
    )
    require(len(set(pids)) == 2, f"the {operation} stage did not use two processes")
    require(
        len(set(parents)) == 1,
        f"the two {operation} processes were not launched by one shell action",
    )


def validate_searches(searches: list[dict]) -> list[str]:
    searches_by_pair: dict[tuple[str, str], str] = {}
    for entry in searches:
        arguments = entry.get("arguments")
        require(isinstance(arguments, dict), "a search has invalid arguments")
        require(
            set(arguments) == {"name", "location"},
            "a search was not an exact name-and-location search",
        )
        pair = (arguments.get("name"), arguments.get("location"))
        require(
            pair in REQUESTED and pair not in searches_by_pair,
            "searches did not cover each requested branch exactly once",
        )
        response = entry.get("response")
        matches = response.get("matches") if isinstance(response, dict) else None
        require(
            isinstance(matches, list) and len(matches) == 1,
            "each controlled search must produce exactly one match",
        )
        match = matches[0]
        require(
            isinstance(match, dict)
            and match.get("name") == pair[0]
            and match.get("location") == pair[1],
            "a search result does not match its requested branch",
        )
        stable_id = match.get("id")
        require(
            isinstance(stable_id, str) and stable_id,
            "a unique search result has no stable ID",
        )
        searches_by_pair[pair] = stable_id
    return [searches_by_pair[pair] for pair in REQUESTED]


def validate_gets(gets: list[dict], searched_ids: list[str]) -> list[dict]:
    gets_by_id: dict[str, dict] = {}
    for entry in gets:
        arguments = entry.get("arguments")
        require(
            isinstance(arguments, dict) and set(arguments) == {"id"},
            "a get must use only one returned stable ID",
        )
        stable_id = arguments.get("id")
        require(
            stable_id in searched_ids and stable_id not in gets_by_id,
            "gets must use each branch's unique searched ID exactly once",
        )
        response = entry.get("response")
        require(isinstance(response, dict), "a get returned an invalid response")
        gets_by_id[stable_id] = response
    require(
        set(gets_by_id) == set(searched_ids),
        "both independently resolved branches must be retrieved",
    )

    records = []
    for (name, location), stable_id in zip(REQUESTED, searched_ids):
        record = gets_by_id[stable_id]
        require(record.get("id") == stable_id, "a get returned a different stable ID")
        require(
            record.get("name") == name and record.get("location") == location,
            "a get returned a different requested shipment",
        )
        status = record.get("status")
        require(
            isinstance(status, str) and status,
            "a successful get did not return a status",
        )
        records.append(record)
    return records


def expected_report(records: list[dict]) -> str:
    lines = [
        f"{record['name']} at {record['location']} — ID {record['id']}; "
        f"status {record['status']}."
        for record in records
    ]
    first_status = records[0]["status"]
    second_status = records[1]["status"]
    if first_status == second_status:
        lines.append(f"Statuses match: {first_status}.")
    else:
        lines.append(f"Statuses differ: {first_status} versus {second_status}.")
    lines.append("No changes made.")
    return "\n".join(lines) + "\n"


def verify() -> None:
    check_protected_inputs()
    entries = read_journal()
    require(
        all(entry.get("operation") in {"search", "get"} for entry in entries),
        "a forbidden shipment operation was used",
    )
    searches = [entry for entry in entries if entry.get("operation") == "search"]
    gets = [entry for entry in entries if entry.get("operation") == "get"]
    validate_parallel_stage(searches, "search")
    validate_parallel_stage(gets, "get")
    require(
        searches[0]["parent_pid"] != gets[0]["parent_pid"],
        "searches and gets must be separate shell-tool actions",
    )
    require(
        min(entry["started_ns"] for entry in gets)
        >= max(entry["ended_ns"] for entry in searches),
        "a get began before both searches returned",
    )
    searched_ids = validate_searches(searches)
    records = validate_gets(gets, searched_ids)

    require(REPORT.is_file(), "exception-board.txt is missing")
    report = REPORT.read_text(encoding="utf-8")
    require(
        report == expected_report(records),
        "exception-board.txt is not the required evidence-grounded four-line reply",
    )
    require(len(re.findall(r"\S+", report)) < 75, "the reply is not under 75 words")
    require(report.endswith("No changes made.\n"), "the required final sentence is missing")


def main() -> int:
    try:
        verify()
    except (OSError, sqlite3.Error, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: parallel dependency-aware read-only shipment audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
