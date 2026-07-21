#!/usr/bin/env python3
"""Protected, deterministic acceptance checks for the meeting audit task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "meetings.sqlite3"
AUDIT_PATH = ROOT / "audit.log"
REPORT_PATH = ROOT / "regional-brief.md"
REQUESTED = (
    ("Quarterly Roadmap Review", "Denver HQ"),
    ("Budget Close Readout", "Chicago Hub"),
)
FORBIDDEN = {"list", "preferences", "availability", "create", "update", "cancel", "notify"}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_expected_records() -> list[dict[str, str]]:
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        records: list[dict[str, str]] = []
        for name, location in REQUESTED:
            rows = db.execute(
                """SELECT stable_id, name, location, status, meeting_date, owner, notes
                   FROM meetings
                   WHERE name = ? COLLATE NOCASE
                     AND location = ? COLLATE NOCASE
                   ORDER BY stable_id""",
                (name, location),
            ).fetchall()
            require(len(rows) == 1, f"protected fixture must resolve exactly once: {name!r}")
            records.append(dict(rows[0]))
        return records
    finally:
        db.close()


def load_events() -> list[dict[str, object]]:
    require(AUDIT_PATH.is_file(), "no meeting-client invocation audit was produced")
    events: list[dict[str, object]] = []
    for number, line in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(f"audit line {number} is not valid JSON: {error}") from error
        require(isinstance(event, dict), f"audit line {number} is not an object")
        events.append(event)
    return events


def interval(event: dict[str, object]) -> tuple[int, int]:
    start, end = event.get("started_ns"), event.get("ended_ns")
    require(isinstance(start, int) and isinstance(end, int) and start < end,
            "audit event has an invalid execution interval")
    return start, end


def overlap(events: list[dict[str, object]], label: str) -> None:
    require(len(events) == 2, f"expected two {label} executions")
    batches = {event.get("concurrency_batch") for event in events}
    require(len(batches) == 1 and None not in batches,
            f"the two {label} executions did not share one concurrency rendezvous")
    first, second = interval(events[0]), interval(events[1])
    require(max(first[0], second[0]) < min(first[1], second[1]),
            f"the two {label} executions did not run concurrently")


def verify_events(events: list[dict[str, object]], records: list[dict[str, str]]) -> None:
    require(events, "meeting client was not used")
    operations = [event.get("operation") for event in events]
    used_forbidden = sorted({str(op) for op in operations if op in FORBIDDEN})
    require(not used_forbidden, "forbidden meeting operations used: " + ", ".join(used_forbidden))
    require(len(events) == 4, "the audit must contain exactly two searches and two gets")
    require(all(event.get("ok") is True for event in events), "every required client call must succeed")

    searches = [event for event in events if event.get("operation") == "search"]
    gets = [event for event in events if event.get("operation") == "get"]
    require(len(searches) == 2 and len(gets) == 2,
            "the audit must contain exactly two searches followed by two gets")

    expected_by_pair = {
        (record["name"], record["location"]): record["stable_id"] for record in records
    }
    seen_pairs: set[tuple[str, str]] = set()
    for event in searches:
        evidence = event.get("evidence")
        require(isinstance(evidence, dict), "search audit evidence is missing")
        pair = (evidence.get("name"), evidence.get("location"))
        require(pair in expected_by_pair, "search used the wrong name or location")
        require(pair not in seen_pairs, "a requested search was repeated")
        seen_pairs.add(pair)
        require(evidence.get("match_count") == 1, "a get may follow only one exact search match")
        require(evidence.get("stable_ids") == [expected_by_pair[pair]],
                "search did not resolve the protected stable ID")
    require(seen_pairs == set(expected_by_pair), "both requested searches are required")

    expected_ids = {record["stable_id"] for record in records}
    seen_ids: set[str] = set()
    for event in gets:
        evidence = event.get("evidence")
        require(isinstance(evidence, dict), "get audit evidence is missing")
        stable_id = evidence.get("stable_id")
        require(stable_id in expected_ids, "get used an unresolved stable ID")
        require(stable_id not in seen_ids, "a requested get was repeated")
        seen_ids.add(str(stable_id))
        record = next(record for record in records if record["stable_id"] == stable_id)
        expected_digest = hashlib.sha256(
            json.dumps(record, sort_keys=True).encode()
        ).hexdigest()
        require(evidence.get("found") is True
                and evidence.get("record_digest") == expected_digest,
                "get did not return the protected full record")
    require(seen_ids == expected_ids, "both uniquely resolved records must be retrieved")

    overlap(searches, "search")
    overlap(gets, "get")
    latest_search_end = max(interval(event)[1] for event in searches)
    earliest_get_start = min(interval(event)[0] for event in gets)
    require(earliest_get_start > latest_search_end,
            "retrieval began before both independent searches completed")


def verify_report(records: list[dict[str, str]]) -> None:
    require(REPORT_PATH.is_file(), "regional-brief.md was not created")
    text = REPORT_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    require(len(lines) == 3, "regional-brief.md must contain exactly three lines")
    require(all(line.startswith("- ") and len(line) > 2 for line in lines),
            "every report line must be a non-empty '- ' Markdown bullet")

    for index, record in enumerate(records):
        folded = lines[index].casefold()
        for field in ("name", "location", "status"):
            require(record[field].casefold() in folded,
                    f"record bullet {index + 1} is missing its {field}")
        require(record["stable_id"] in lines[index],
                f"record bullet {index + 1} is missing its exact stable_id")

    comparison = lines[2].casefold()
    for record in records:
        require(record["status"].casefold() in comparison,
                "comparison bullet must name both retrieved statuses")
    statuses_match = records[0]["status"].casefold() == records[1]["status"].casefold()
    if statuses_match:
        require(any(word in comparison for word in ("same", "match", "equal")),
                "comparison bullet must state that the statuses match")
    else:
        require(any(word in comparison for word in ("differ", "different", "not the same")),
                "comparison bullet must state that the statuses differ")


def main() -> int:
    try:
        records = load_expected_records()
        verify_events(load_events(), records)
        verify_report(records)
    except (OSError, sqlite3.Error, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: parallel gated meeting audit and exact three-bullet brief verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
