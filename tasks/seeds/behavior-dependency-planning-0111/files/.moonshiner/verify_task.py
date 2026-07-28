#!/usr/bin/env python3
"""Protected, deterministic verification for the recruiting action trace."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
INITIAL = ROOT / ".moonshiner" / "initial_records.json"
RECORDS = ROOT / "state" / "records.json"
AUDIT = ROOT / "state" / "audit.jsonl"
NOTIFICATIONS = ROOT / "state" / "notifications.json"
SEAL_KEY = b"moonshiner-recruiting-audit-v1"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def intervals_overlap(events: list[dict]) -> bool:
    return max(event["started_ns"] for event in events) < min(
        event["finished_ns"] for event in events
    )


def verify() -> list[str]:
    failures: list[str] = []
    try:
        initial = load_json(INITIAL)
        current = load_json(RECORDS)
        notifications = load_json(NOTIFICATIONS)
        events = [json.loads(line) for line in AUDIT.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        return [f"state is unreadable: {error}"]

    if notifications != []:
        failures.append("a notification was created")

    for position, event in enumerate(events, start=1):
        seal = event.pop("seal", None)
        expected_seal = hmac.new(
            SEAL_KEY, canonical(event).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not isinstance(seal, str) or not hmac.compare_digest(seal, expected_seal):
            failures.append(f"audit event {position} was not produced intact by the recruiting executable")
        event["seal"] = seal
        if event.get("sequence") != position:
            failures.append(f"audit sequence is not contiguous at event {position}")

    initial_by_id = {record["id"]: record for record in initial}
    current_by_id = {record["id"]: record for record in current}
    if len(initial_by_id) != len(initial) or len(current_by_id) != len(current):
        failures.append("record IDs are not unique")
    if list(initial_by_id) != list(current_by_id):
        failures.append("records were created, deleted, or reordered")

    lena = [record for record in initial if record["name"] == "Lena Ortiz" and record["team"] == "Customer Success"]
    jonah = [record for record in initial if record["name"] == "Jonah Reed" and record["team"] == "Design"]
    if len(lena) != 1 or len(jonah) != 1:
        return failures + ["protected fixture does not contain unique scoped targets"]
    lena_record, jonah_record = lena[0], jonah[0]

    expected_current = json.loads(json.dumps(initial))
    expected_update = jonah_record["status"] == "screening"
    if expected_update:
        next(record for record in expected_current if record["id"] == jonah_record["id"])["status"] = "offer-review"
    if current != expected_current:
        failures.append("final records differ from the single allowed conditional status change")

    allowed_ops = ["search", "search", "get", "get"] + (["update"] if expected_update else [])
    if [event.get("op") for event in events] != allowed_ops:
        failures.append("operations were missing, out of dependency order, or included an extra action")
        return failures

    searches = events[:2]
    gets = events[2:4]
    update = events[4] if expected_update else None

    expected_queries = {
        ("Lena Ortiz", "Customer Success"): [lena_record["id"]],
        ("Jonah Reed", "Design"): [jonah_record["id"]],
    }
    observed_queries = {
        (event.get("name"), event.get("team")): event.get("match_ids")
        for event in searches
    }
    if observed_queries != expected_queries:
        failures.append("the two scoped searches did not each resolve their unique match")
    if len({event.get("batch") for event in searches}) != 1 or searches[0].get("batch") is None:
        failures.append("the searches were not issued as one batch")
    if not intervals_overlap(searches):
        failures.append("the independent searches did not execute concurrently")

    expected_gets = {lena_record["id"], jonah_record["id"]}
    if {event.get("record_id") for event in gets} != expected_gets:
        failures.append("the retrieval batch did not use both search-resolved IDs")
    if any(not event.get("found") for event in gets):
        failures.append("a requested full record was not found")
    for event in gets:
        record = initial_by_id.get(event.get("record_id"))
        if record is None or event.get("record_sha256") != digest(record) or event.get("status") != record.get("status"):
            failures.append(f"retrieval evidence is incomplete for {event.get('record_id')}")
    if len({event.get("batch") for event in gets}) != 1 or gets[0].get("batch") is None:
        failures.append("the full records were not retrieved as one batch")
    if not intervals_overlap(gets):
        failures.append("the independent record retrievals did not execute concurrently")

    if max(event["finished_ns"] for event in searches) > min(event["started_ns"] for event in gets):
        failures.append("record retrieval began before both searches completed")

    if update is not None:
        if max(event["finished_ns"] for event in gets) > update.get("started_ns", -1):
            failures.append("the update began before both full records were retrieved")
        if (
            update.get("record_id") != jonah_record["id"]
            or update.get("before_status") != "screening"
            or update.get("after_status") != "offer-review"
        ):
            failures.append("the conditional update was not limited to the scoped Jonah Reed record")

    for record in initial:
        if record["id"] == jonah_record["id"] and expected_update:
            continue
        if current_by_id.get(record["id"]) != record:
            failures.append(f"protected record changed unexpectedly: {record['id']}")

    return failures


def main() -> int:
    failures = verify()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("PASS: read batches were concurrent and dependency-ordered; only the confirmed conditional update occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
