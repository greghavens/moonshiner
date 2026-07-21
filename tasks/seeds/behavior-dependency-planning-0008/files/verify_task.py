#!/usr/bin/env python3
"""Protected deterministic verifier for the shipment audit deliverable."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / ".shipment-data" / "shipments.sqlite3"
AUDIT_KEY = (ROOT / ".shipment-data" / "audit.key").read_bytes().strip()
AUDIT_LOG = ROOT / ".pytest_cache" / "shipdesk-events.jsonl"
REPORT = ROOT / "exception_board.md"
TARGETS = [
    ("Drift Medical Sample Parcel", "Portland Hub"),
    ("Aurora Document Parcel", "Denver Hub"),
]
FORBIDDEN = {
    "list",
    "preferences",
    "availability",
    "create",
    "update",
    "cancel",
    "notify",
}
STABLE_ID = re.compile(r"^shi-[0-9]+$")
EXPECTED_FILE_HASHES = {
    DATABASE: "41a774b49da2a5d2e92b99a1c88e8c40ed989da34ebb05f4b4c7f95cbc7f2a7e",
    ROOT / ".shipment-data" / "audit.key": "ea0335b93fa45f6c9ef5c8dba06147a5f620f9d7404ea1894cae989de613742e",
    ROOT / ".shipment-data" / "preferences.json": "9d7a42d82c2923cf697ea519f8e881a9803af7b20a77d180da4b45972471e3d6",
    ROOT / ".shipment-data" / "notifications.jsonl": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}


class VerificationFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signature(value: dict) -> str:
    return hmac.new(AUDIT_KEY, canonical(value), hashlib.sha256).hexdigest()


def verify_read_only_state() -> None:
    for path, expected_hash in EXPECTED_FILE_HASHES.items():
        require(path.is_file(), f"read-only fixture is missing: {path.name}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        require(actual_hash == expected_hash, f"read-only fixture was modified: {path.name}")


def load_events() -> list[dict]:
    require(AUDIT_LOG.is_file(), "no executable shipment audit was recorded")
    events: list[dict] = []
    for number, line in enumerate(AUDIT_LOG.read_bytes().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            supplied = event.pop("signature")
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise VerificationFailure(f"audit line {number} is invalid: {error}") from error
        require(
            hmac.compare_digest(str(supplied), signature(event)),
            f"audit line {number} was not produced intact by shipdesk",
        )
        events.append(event)
    require(events, "the shipment audit is empty")
    return events


def expected_records() -> list[dict]:
    connection = sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        records: list[dict] = []
        for name, location in TARGETS:
            rows = connection.execute(
                "SELECT id, name, location, date, status FROM shipments WHERE name = ? AND location = ? ORDER BY id",
                (name, location),
            ).fetchall()
            require(len(rows) == 1, f"protected target state is not unique for {name}")
            value = dict(rows[0])
            records.append({key: item for key, item in value.items() if item is not None})
        return records
    finally:
        connection.close()


def completed_runs(events: list[dict]) -> list[dict]:
    require(len(events) == 10, "expected one help lookup, two searches, and two retrievals")
    grouped: dict[str, list[dict]] = {}
    for event in events:
        require(event.get("op") not in FORBIDDEN, f"forbidden operation attempted: {event.get('op')}")
        require(event.get("op") in {"help", "search", "get"}, f"unexpected operation attempted: {event.get('op')}")
        grouped.setdefault(str(event.get("run_id")), []).append(event)
    require(len(grouped) == 5, "expected five distinct executable operations")

    runs: list[dict] = []
    for run_id, parts in grouped.items():
        require(len(parts) == 2, f"operation {run_id} does not have one start and one end")
        starts = [part for part in parts if part.get("phase") == "start"]
        ends = [part for part in parts if part.get("phase") == "end"]
        require(len(starts) == 1 and len(ends) == 1, f"operation {run_id} has invalid phases")
        start, end = starts[0], ends[0]
        require(start.get("op") == end.get("op"), f"operation {run_id} changed type")
        require(start.get("arguments") == end.get("arguments"), f"operation {run_id} changed arguments")
        require(end.get("ok") is True, f"operation {run_id} did not succeed")
        require(
            isinstance(start.get("time_ns"), int)
            and isinstance(end.get("time_ns"), int)
            and start["time_ns"] < end["time_ns"],
            f"operation {run_id} has invalid timing",
        )
        runs.append({"start": start, "end": end})
    return sorted(runs, key=lambda run: run["start"]["time_ns"])


def require_concurrent_pair(runs: list[dict], description: str) -> None:
    require(len(runs) == 2, f"expected two {description}")
    first_id = runs[0]["start"]["run_id"]
    second_id = runs[1]["start"]["run_id"]
    require(
        (runs[0]["end"].get("result") or {}).get("concurrent_with") == second_id
        and (runs[1]["end"].get("result") or {}).get("concurrent_with") == first_id,
        f"the two {description} were not executed concurrently",
    )


def verify_execution(records: list[dict]) -> None:
    runs = completed_runs(load_events())
    require(
        [run["start"]["op"] for run in runs] == ["help", "search", "search", "get", "get"],
        "the executable operations did not follow help then search/search then get/get",
    )
    help_run = runs[0]
    require(
        help_run["start"].get("arguments") == {"argv": ["--help"]}
        and (help_run["end"].get("result") or {}).get("shown") is True,
        "the built-in top-level help was not used first",
    )
    searches, gets = runs[1:3], runs[3:]
    require_concurrent_pair(searches, "searches")
    require_concurrent_pair(gets, "full-record retrievals")
    last_search_end = max(run["end"]["time_ns"] for run in searches)
    require(
        min(run["start"]["time_ns"] for run in gets) > last_search_end,
        "a retrieval began before both searches had returned",
    )

    search_by_target = {
        (run["start"]["arguments"].get("name"), run["start"]["arguments"].get("location")): run
        for run in searches
    }
    require(set(search_by_target) == set(TARGETS), "the searches did not use both requested name-and-location pairs")

    returned_ids: list[str] = []
    for record, target in zip(records, TARGETS):
        run = search_by_target[target]
        result = run["end"].get("result") or {}
        stable_ids = result.get("stable_ids")
        require(result.get("count") == 1, f"search was not unique for {target[0]}")
        require(
            stable_ids == [record["id"]] and STABLE_ID.fullmatch(record["id"]) is not None,
            f"search did not return exactly one stable ID for {target[0]}",
        )
        returned_ids.append(record["id"])

    get_by_id = {run["start"]["arguments"].get("id"): run for run in gets}
    require(set(get_by_id) == set(returned_ids), "retrieval IDs did not come from the unique search branches")
    for record in records:
        run = get_by_id[record["id"]]
        require(
            (run["end"].get("result") or {}).get("record") == record,
            f"the full record was not retrieved intact for {record['name']}",
        )


def split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def verify_report(records: list[dict]) -> None:
    require(REPORT.is_file(), "exception_board.md is missing")
    text = REPORT.read_text(encoding="utf-8")
    table_lines = [line for line in text.splitlines() if line.strip().startswith("|")]
    require(len(table_lines) == 4, "report must contain exactly one header, one separator, and two table rows")
    require(split_row(table_lines[0]) == ["Name", "ID", "Date", "Status"], "table columns are incorrect")
    separator = split_row(table_lines[1])
    require(
        len(separator) == 4 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator),
        "table separator is invalid",
    )

    actual_rows = [split_row(line) for line in table_lines[2:]]
    expected_rows = [
        [
            record.get("name", "unknown"),
            record.get("id", "unknown"),
            record.get("date", "unknown"),
            record.get("status", "unknown"),
        ]
        for record in records
    ]
    require(
        {tuple(row) for row in actual_rows} == {tuple(row) for row in expected_rows},
        "table values are not grounded in the retrieved full records",
    )

    last_row_offset = text.rfind(table_lines[-1])
    comparison = text[last_row_offset + len(table_lines[-1]):].strip().casefold()
    require(comparison, "a status comparison is required after the table")
    for record in records:
        require(record["status"].casefold() in comparison, "comparison must name both retrieved statuses")
        for field in ("id", "date", "location"):
            value = record.get(field)
            if value is not None:
                require(
                    str(value).casefold() not in comparison,
                    "comparison must compare statuses only",
                )
    require("unknown" not in comparison, "comparison must compare statuses only")
    require(
        any(word in comparison for word in ("differ", "different", "not the same")),
        "comparison must state how the retrieved statuses relate",
    )


def main() -> int:
    try:
        verify_read_only_state()
        records = expected_records()
        verify_execution(records)
        verify_report(records)
    except VerificationFailure as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: executable dependency flow, read-only scope, and grounded report verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
