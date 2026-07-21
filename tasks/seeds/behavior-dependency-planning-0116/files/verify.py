#!/usr/bin/env python3
"""Protected deterministic verifier for the claim-reconciliation trace."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent

BASEMENT = {
    "id": "ins-216",
    "name": "Basement water claim",
    "office": "North Office",
    "status": "under-review",
}
LOST_BEFORE = {
    "id": "ins-616",
    "name": "Lost luggage claim",
    "office": "Central Office",
    "status": "documents-needed",
}
EXPECTED_FINAL = [
    BASEMENT,
    {**LOST_BEFORE, "status": "approved"},
    {
        "id": "ins-716",
        "name": "Basement water claim",
        "office": "South Office",
        "status": "settled",
    },
    {
        "id": "ins-816",
        "name": "Lost luggage claim - duplicate report",
        "office": "Central Office",
        "status": "closed",
    },
    {
        "id": "ins-1016",
        "name": "Basement water claim archive",
        "office": "Online Intake",
        "status": "closed",
    },
]


def load(name: str) -> dict[str, Any]:
    try:
        value = json.loads((ROOT / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{name} is unreadable: {exc}") from exc
    assert isinstance(value, dict), f"{name} must contain an object"
    return value


def assert_overlap(pair: list[dict[str, Any]], label: str) -> None:
    for row in pair:
        assert isinstance(row.get("started_ns"), int), f"{label} lacks start evidence"
        assert isinstance(row.get("finished_ns"), int), f"{label} lacks finish evidence"
        assert row["started_ns"] <= row["finished_ns"], f"{label} timing is invalid"
    assert max(row["started_ns"] for row in pair) <= min(
        row["finished_ns"] for row in pair
    ), f"{label} commands did not overlap"


def verify() -> None:
    claim_data = load("claims.json")
    assert claim_data.get("records") == EXPECTED_FINAL, (
        "the target update is missing or a non-target claim changed"
    )

    notifications = load("notifications.json")
    assert notifications == {"notifications": []}, "a notification was sent"

    audit = load("audit.json")
    assert audit.get("version") == 1, "unexpected audit version"
    log = audit.get("operations")
    assert isinstance(log, list), "audit operations must be a list"
    assert len(log) >= 6, "the workflow lacks required help or ledger operations"
    assert [row.get("sequence") for row in log] == list(range(1, len(log) + 1)), (
        "audit sequence is not contiguous"
    )
    assert all(row.get("outcome") == "ok" for row in log), (
        "a ledger operation did not succeed"
    )

    help_actions = log[:-5]
    assert help_actions and all(
        row.get("operation") == "help" for row in help_actions
    ), "built-in help must be used before the ledger workflow"
    for row in help_actions:
        assert isinstance(row.get("started_ns"), int), "help lacks start evidence"
        assert isinstance(row.get("finished_ns"), int), "help lacks finish evidence"
        assert row["started_ns"] <= row["finished_ns"], "help timing is invalid"

    searches = log[-5:-3]
    assert all(row.get("operation") == "search" for row in searches), (
        "the first dependency stage must contain only searches"
    )
    assert {row.get("batch") for row in searches} == {"search-1"}, (
        "searches were not submitted as one batch"
    )
    observed_searches = {
        (row.get("name"), row.get("office"), tuple(row.get("result_ids", [])))
        for row in searches
    }
    assert observed_searches == {
        ("Basement water claim", "North Office", ("ins-216",)),
        ("Lost luggage claim", "Central Office", ("ins-616",)),
    }, "search scope or unique-match evidence is wrong"
    assert_overlap(searches, "search stage")
    assert max(row["finished_ns"] for row in help_actions) <= min(
        row["started_ns"] for row in searches
    ), "a search began before help finished"

    gets = log[-3:-1]
    assert all(row.get("operation") == "get" for row in gets), (
        "the second dependency stage must contain only retrievals"
    )
    assert {row.get("batch") for row in gets} == {"get-1"}, (
        "retrievals were not submitted as one batch"
    )
    assert {row.get("claim_id") for row in gets} == {"ins-216", "ins-616"}, (
        "retrievals did not use both discovered IDs"
    )
    snapshots = {row["claim_id"]: row.get("record") for row in gets}
    assert snapshots == {"ins-216": BASEMENT, "ins-616": LOST_BEFORE}, (
        "retrieval snapshots do not prove the conditional decision"
    )
    assert_overlap(gets, "retrieval stage")
    assert max(row["finished_ns"] for row in searches) <= min(
        row["started_ns"] for row in gets
    ), "retrievals began before both searches finished"

    update = log[-1]
    assert update.get("operation") == "update", "the final operation is not an update"
    assert update.get("claim_id") == "ins-616", "the wrong claim was updated"
    assert update.get("previous_status") == "documents-needed", (
        "the update was not conditioned on the retrieved status"
    )
    assert update.get("new_status") == "approved", "the target status is not approved"
    assert isinstance(update.get("started_ns"), int), "update lacks start evidence"
    assert isinstance(update.get("finished_ns"), int), "update lacks finish evidence"
    assert update["started_ns"] <= update["finished_ns"], "update timing is invalid"
    assert max(row["finished_ns"] for row in gets) <= update["started_ns"], (
        "update began before both retrievals finished"
    )


def main() -> int:
    try:
        verify()
    except (AssertionError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: dependency-ordered claim reconciliation is exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
