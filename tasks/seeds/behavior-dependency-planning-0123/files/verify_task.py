#!/usr/bin/env python3
"""Protected deterministic verification for the reconciliation task."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent

BASE_RECORDS = {
    "hos-223": ("Cedar Room workshop", "Raleigh", "confirmed", 18),
    "hos-623": ("Garden Terrace dinner", "Portland", "held", 24),
    "hos-1023": ("Cedar Room workshop archive", "Savannah", "closed", 12),
    "hos-1123": ("Cedar Room workshop", "Austin", "completed", 20),
    "hos-1323": ("Cedar Room workshop follow-up", "Raleigh", "held", 10),
    "hos-1623": ("Garden Terrace dinner", "Seattle", "confirmed", 30),
    "hos-1723": ("Garden Terrace dinner rehearsal", "Portland", "held", 8),
    "hos-1923": ("Garden Terrace brunch", "Portland", "waitlisted", 16),
}
CEDAR_ID = "hos-223"
GARDEN_ID = "hos-623"
SEARCH_REQUESTS = {
    ("Cedar Room workshop", "Raleigh"): CEDAR_ID,
    ("Garden Terrace dinner", "Portland"): GARDEN_ID,
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def load_json(name: str) -> dict:
    try:
        with (ROOT / name).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {name}: {error}")
        raise AssertionError("unreachable")


def check_workspace_scope() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changed = set()
    for line in result.stdout.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.add(path)
    allowed = {"audit.json", "ledger.json"}
    unexpected = sorted(changed - allowed)
    if unexpected:
        fail("unexpected lasting workspace changes: " + ", ".join(unexpected))


def check_records(ledger: dict) -> None:
    if ledger.get("notifications") != []:
        fail("a notification was created")
    records = ledger.get("records")
    if not isinstance(records, list) or len(records) != len(BASE_RECORDS):
        fail("record set changed")
    observed = {}
    for record in records:
        try:
            observed[record["id"]] = (
                record["name"],
                record["location"],
                record["status"],
                record["party_size"],
            )
        except (KeyError, TypeError):
            fail("a record has an invalid shape")
    expected = dict(BASE_RECORDS)
    target = list(expected[GARDEN_ID])
    target[2] = "waitlisted"
    expected[GARDEN_ID] = tuple(target)
    if observed != expected:
        changed = sorted(
            record_id
            for record_id in set(observed) | set(expected)
            if observed.get(record_id) != expected.get(record_id)
        )
        fail("wrong final record state; changed IDs: " + ", ".join(changed))


def require_parallel_batch(operations: list[dict], action: str) -> list[dict]:
    batch = [operation for operation in operations if operation.get("action") == action]
    if len(batch) != 2:
        fail(f"expected exactly two {action} operations, found {len(batch)}")
    tokens = {operation.get("batch_token") for operation in batch}
    if len(tokens) != 1 or None in tokens or "" in tokens:
        fail(f"{action} operations were not paired in one parallel batch")
    try:
        latest_start = max(operation["started_ns"] for operation in batch)
        earliest_completion = min(operation["completed_ns"] for operation in batch)
    except (KeyError, TypeError):
        fail(f"{action} audit timing is incomplete")
    if latest_start >= earliest_completion:
        fail(f"{action} operations did not overlap")
    if any(operation.get("success") is not True for operation in batch):
        fail(f"a {action} operation did not succeed")
    return batch


def check_audit(audit: dict) -> None:
    operations = audit.get("operations")
    if not isinstance(operations, list):
        fail("audit operations are missing")
    if len(operations) != 5:
        fail(f"expected exactly five service operations, found {len(operations)}")
    ids = [operation.get("id") for operation in operations]
    if any(not isinstance(operation_id, str) or not operation_id for operation_id in ids):
        fail("an audit operation ID is invalid")
    if len(set(ids)) != len(ids):
        fail("audit operation IDs are not unique")

    searches = require_parallel_batch(operations, "search")
    observed_searches = {}
    for operation in searches:
        request = operation.get("request", {})
        key = (request.get("name"), request.get("location"))
        results = operation.get("result_ids")
        if key in observed_searches:
            fail("a search was duplicated")
        observed_searches[key] = results
    expected_searches = {key: [record_id] for key, record_id in SEARCH_REQUESTS.items()}
    if observed_searches != expected_searches:
        fail("search requests or exact-match results were wrong")

    retrievals = require_parallel_batch(operations, "get")
    observed_gets = {
        operation.get("request", {}).get("id"): operation.get("result_ids")
        for operation in retrievals
    }
    expected_gets = {CEDAR_ID: [CEDAR_ID], GARDEN_ID: [GARDEN_ID]}
    if observed_gets != expected_gets:
        fail("retrieval requests or results were wrong")

    search_completed = max(operation["completed_ns"] for operation in searches)
    get_started = min(operation["started_ns"] for operation in retrievals)
    if get_started <= search_completed:
        fail("retrieval began before both searches completed")

    updates = [operation for operation in operations if operation.get("action") == "update"]
    if len(updates) != 1:
        fail(f"expected exactly one update operation, found {len(updates)}")
    update = updates[0]
    if update.get("success") is not True:
        fail("the update did not succeed")
    if update.get("request") != {"id": GARDEN_ID, "status": "waitlisted"}:
        fail("the wrong record or status was updated")
    if update.get("result_ids") != [GARDEN_ID]:
        fail("the update result is inconsistent")
    if update.get("transition") != {"from": "held", "to": "waitlisted"}:
        fail("the conditional held-to-waitlisted transition was not followed")
    try:
        if update["started_ns"] <= max(operation["completed_ns"] for operation in retrievals):
            fail("update began before both retrievals completed")
        if update["completed_ns"] < update["started_ns"]:
            fail("update audit timing is invalid")
    except (KeyError, TypeError):
        fail("update audit timing is incomplete")

    actions = [operation.get("action") for operation in operations]
    if sorted(actions) != ["get", "get", "search", "search", "update"]:
        fail("an extra or unsupported service action was taken")


def main() -> int:
    check_workspace_scope()
    check_records(load_json("ledger.json"))
    check_audit(load_json("audit.json"))
    print("PASS: unique parallel lookups, dependent retrievals, and only the conditional update")
    return 0


if __name__ == "__main__":
    sys.exit(main())
