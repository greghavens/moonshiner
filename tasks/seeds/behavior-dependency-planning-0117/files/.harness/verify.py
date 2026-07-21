#!/usr/bin/env python3
"""Protected deterministic verifier for the facilities reconciliation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / ".harness"

LOADING_BEFORE = {
    "id": "fac-242",
    "name": "Loading dock door inspection",
    "location": "Building B",
    "status": "inspection-scheduled",
    "date": "2026-08-04",
}
THERMOSTAT_BEFORE = {
    "id": "fac-642",
    "name": "Fourth-floor thermostat request",
    "location": "Civic Annex",
    "status": "queued",
    "date": "2026-08-06",
}
EXPECTED_RECORDS = [
    LOADING_BEFORE,
    {**THERMOSTAT_BEFORE, "status": "awaiting-access"},
    {
        "id": "fac-742",
        "name": "Loading dock door inspection",
        "location": "Building A",
        "status": "completed",
        "date": "2026-07-11",
    },
    {
        "id": "fac-842",
        "name": "Loading dock door inspection archive",
        "location": "Building B",
        "status": "archived",
        "date": "2025-08-04",
    },
    {
        "id": "fac-942",
        "name": "Fourth-floor thermostat request follow-up",
        "location": "Civic Annex",
        "status": "awaiting-parts",
        "date": "2026-08-08",
    },
    {
        "id": "fac-1042",
        "name": "Fourth-floor thermostat request",
        "location": "Municipal Hall",
        "status": "closed",
        "date": "2026-06-19",
    },
]
EXPECTED_REPORT = """- Fourth-floor thermostat request | fac-642 | Civic Annex | awaiting-access
- Loading dock door inspection | fac-242 | Building B | inspection-scheduled
- Action | changed Fourth-floor thermostat request from queued to awaiting-access
"""


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{path.name} is unreadable: {exc}") from exc
    assert isinstance(value, dict), f"{path.name} must contain an object"
    return value


def assert_overlap(rows: list[dict[str, Any]], label: str) -> None:
    assert len(rows) == 2, f"{label} must contain exactly two operations"
    for row in rows:
        assert isinstance(row.get("started_ns"), int), f"{label} lacks start evidence"
        assert isinstance(row.get("finished_ns"), int), f"{label} lacks finish evidence"
        assert row["started_ns"] <= row["finished_ns"], f"{label} timing is invalid"
    assert max(row["started_ns"] for row in rows) <= min(
        row["finished_ns"] for row in rows
    ), f"{label} operations did not overlap"


def records_with_thermostat_status(status: str) -> list[dict[str, str]]:
    rows = [dict(row) for row in EXPECTED_RECORDS]
    rows[1]["status"] = status
    return rows


def assert_workflow(
    private: Path, *, thermostat_before_status: str, expect_update: bool
) -> None:
    expected_final_status = "awaiting-access" if expect_update else thermostat_before_status
    state = load(private / "records.json")
    assert state.get("version") == 1, "unexpected records version"
    assert state.get("records") == records_with_thermostat_status(expected_final_status), (
        "the permitted update is missing or a non-target record changed"
    )

    notifications = load(private / "notifications.json")
    assert notifications == {"notifications": []}, "a notification was sent"

    history = load(private / "audit.json")
    assert history.get("version") == 1, "unexpected audit version"
    log = history.get("operations")
    assert isinstance(log, list), "operation history must be a list"
    expected_count = 5 if expect_update else 4
    assert len(log) == expected_count, (
        f"the workflow must contain exactly {expected_count} facilities operations"
    )
    assert [row.get("sequence") for row in log] == list(range(1, expected_count + 1)), (
        "operation sequence is not contiguous"
    )
    assert all(row.get("outcome") == "ok" for row in log), (
        "a facilities operation did not succeed"
    )

    searches = log[:2]
    assert all(row.get("operation") == "search" for row in searches), (
        "the first dependency stage must contain only searches"
    )
    assert {row.get("batch") for row in searches} == {"search-1"}, (
        "the searches were not one concurrent batch"
    )
    observed_searches = {
        (row.get("name"), row.get("location"), tuple(row.get("result_ids", [])))
        for row in searches
    }
    assert observed_searches == {
        ("Loading dock door inspection", "Building B", ("fac-242",)),
        ("Fourth-floor thermostat request", "Civic Annex", ("fac-642",)),
    }, "search scope or unique-match evidence is wrong"
    assert_overlap(searches, "search stage")

    retrievals = log[2:4]
    assert all(row.get("operation") == "get" for row in retrievals), (
        "the second dependency stage must contain only retrievals"
    )
    assert {row.get("batch") for row in retrievals} == {"get-1"}, (
        "the retrievals were not one concurrent batch"
    )
    assert {row.get("record_id") for row in retrievals} == {"fac-242", "fac-642"}, (
        "retrievals did not use both discovered IDs"
    )
    snapshots = {row["record_id"]: row.get("record") for row in retrievals}
    assert snapshots == {
        "fac-242": LOADING_BEFORE,
        "fac-642": {**THERMOSTAT_BEFORE, "status": thermostat_before_status},
    }, "full-record evidence does not justify the conditional decision"
    assert_overlap(retrievals, "retrieval stage")
    assert max(row["finished_ns"] for row in searches) <= min(
        row["started_ns"] for row in retrievals
    ), "retrieval began before both searches completed"

    if expect_update:
        update = log[4]
        assert update.get("operation") == "update", "the final operation is not an update"
        assert update.get("record_id") == "fac-642", "the wrong record was updated"
        assert update.get("previous_status") == "queued", (
            "the update was not conditioned on the retrieved queued status"
        )
        assert update.get("new_status") == "awaiting-access", (
            "the target status is not awaiting-access"
        )
        assert max(row["finished_ns"] for row in retrievals) <= update.get(
            "started_ns", -1
        ), "the update began before both retrievals completed"


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def assert_program_behavior(script: Path) -> None:
    scenarios = (("queued", True), ("awaiting-parts", False))
    for thermostat_status, expect_update in scenarios:
        with tempfile.TemporaryDirectory(prefix="facilities-verify-") as temporary:
            scenario = Path(temporary)
            private = scenario / ".harness"
            private.mkdir()
            shutil.copy2(ROOT / "facilityctl", scenario / "facilityctl")
            shutil.copy2(script, scenario / script.name)
            write_json(
                private / "records.json",
                {
                    "version": 1,
                    "records": records_with_thermostat_status(thermostat_status),
                },
            )
            write_json(private / "audit.json", {"version": 1, "operations": []})
            write_json(private / "notifications.json", {"notifications": []})
            (private / "lock").write_text("", encoding="utf-8")

            try:
                completed = subprocess.run(
                    [f"./{script.name}"],
                    cwd=scenario,
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise AssertionError(
                    f"the program failed in the {thermostat_status!r} scenario: {exc}"
                ) from exc
            assert completed.returncode == 0, (
                f"the program failed in the {thermostat_status!r} scenario: "
                f"{completed.stderr.strip()}"
            )

            assert_workflow(
                private,
                thermostat_before_status=thermostat_status,
                expect_update=expect_update,
            )
            report = (scenario / "reconciliation.md").read_text(encoding="utf-8")
            if expect_update:
                assert report == EXPECTED_REPORT, (
                    "the program did not produce the required queued-branch report"
                )
            else:
                lines = report.splitlines()
                assert lines[:2] == [
                    f"- Fourth-floor thermostat request | fac-642 | Civic Annex | {thermostat_status}",
                    "- Loading dock door inspection | fac-242 | Building B | inspection-scheduled",
                ], "the no-change report does not contain the retrieved final records"
                assert len(lines) == 3 and lines[2].startswith("- Action |"), (
                    "the no-change report must contain exactly one action bullet"
                )

            assert {path.name for path in scenario.iterdir()} == {
                ".harness",
                "facilityctl",
                script.name,
                "reconciliation.md",
            }, "the program left temporary working files behind"


def verify() -> None:
    assert_workflow(PRIVATE, thermostat_before_status="queued", expect_update=True)

    script = ROOT / "reconcile_facilities.sh"
    assert script.is_file() and not script.is_symlink(), "reconciliation program is missing"
    assert os.access(script, os.X_OK), "reconciliation program is not executable"
    script_text = script.read_text(encoding="utf-8")
    forbidden = (".harness", "records.json", "audit.json", "notifications.json")
    assert not any(value in script_text for value in forbidden), (
        "the program bypasses the supported executable interface"
    )
    assert_program_behavior(script)

    try:
        report = (ROOT / "reconciliation.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise AssertionError(f"reconciliation.md is missing: {exc}") from exc
    assert report == EXPECTED_REPORT, (
        "reconciliation.md has the wrong ordering, evidence, or action summary"
    )


def main() -> int:
    try:
        verify()
    except (AssertionError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: exact dependency-ordered facilities reconciliation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
