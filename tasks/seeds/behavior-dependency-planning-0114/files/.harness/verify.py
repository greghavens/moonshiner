#!/usr/bin/env python3
"""Protected deterministic verifier for the expense reconciliation task."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "reconcile_expenses.sh"
EXPENSES = ROOT / "expenses"
AIRPORT_ID = "exp-7c4ae1"
WORKSHOP_ID = "exp-b29d63"
EXPECTED_SEARCHES = {
    ("Airport shuttle receipt", "Boston"): AIRPORT_ID,
    ("Workshop parking receipt", "Denver"): WORKSHOP_ID,
}
BASE_RECORDS = {
    "exp-7c4ae1": (
        "Airport shuttle receipt",
        "Boston",
        "reimbursed",
        "2026-05-18",
        "48.00",
        "Nora Chen",
        0,
        0,
    ),
    "exp-b29d63": (
        "Workshop parking receipt",
        "Denver",
        "submitted",
        "2026-06-07",
        "32.50",
        "Elias Brooks",
        0,
        0,
    ),
    "exp-40c8f2": (
        "Airport shuttle receipt",
        "Cambridge",
        "submitted",
        "2026-05-18",
        "39.00",
        "Nora Chen",
        0,
        0,
    ),
    "exp-8ab103": (
        "Airport shuttle receipts",
        "Boston",
        "needs-receipt",
        "2026-05-19",
        "86.00",
        "Nora Chen",
        0,
        0,
    ),
    "exp-a810be": (
        "Workshop parking receipt",
        "Boulder",
        "submitted",
        "2026-06-07",
        "18.00",
        "Elias Brooks",
        0,
        0,
    ),
    "exp-e56c70": (
        "Workshop parking receipt copy",
        "Denver",
        "approved",
        "2026-06-08",
        "32.50",
        "Elias Brooks",
        0,
        0,
    ),
    "exp-170f3d": (
        "Workshop garage receipt",
        "Denver",
        "submitted",
        "2026-06-07",
        "25.00",
        "Mina Patel",
        0,
        0,
    ),
    "exp-13dc99": (
        "Workshop parking receipt",
        "Denver",
        "archived",
        "2025-06-12",
        "29.00",
        "Elias Brooks",
        1,
        0,
    ),
}


def fail(message: str, process: subprocess.CompletedProcess[str] | None = None) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    if process is not None:
        if process.stdout:
            print(f"stdout:\n{process.stdout}", file=sys.stderr)
        if process.stderr:
            print(f"stderr:\n{process.stderr}", file=sys.stderr)
    raise SystemExit(1)


def decode(raw: str | None, context: str) -> object:
    if raw is None:
        fail(f"missing JSON for {context}")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        fail(f"invalid JSON for {context}: {exc}")


def records_snapshot(db: sqlite3.Connection) -> dict[str, tuple[object, ...]]:
    return {
        row["id"]: (
            row["description"],
            row["city"],
            row["status"],
            row["expense_date"],
            row["amount"],
            row["submitted_by"],
            row["archived"],
            row["deleted"],
        )
        for row in db.execute(
            """
            SELECT id, description, city, status, expense_date, amount,
                   submitted_by, archived, deleted
            FROM records
            """
        )
    }


def initialize_case(state: Path, scenario: str) -> None:
    env = os.environ.copy()
    env["PI_EXPENSE_STATE_DIR"] = str(state)
    env["PI_EXPENSE_TEST_SCENARIO"] = scenario
    bootstrap = subprocess.run(
        [
            str(EXPENSES),
            "search",
            "--description",
            "__verifier_bootstrap__",
            "--city",
            "__verifier_bootstrap__",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if bootstrap.returncode != 0:
        fail("could not initialize isolated expense state", bootstrap)
    db = sqlite3.connect(state / "expenses.sqlite3")
    db.execute("DELETE FROM operations")
    db.commit()
    db.close()


def parse_searches(
    rows: list[sqlite3.Row],
) -> dict[tuple[str, str], list[object]]:
    if len(rows) != 2:
        fail(f"expected exactly two searches, found {len(rows)}")
    matches_by_search: dict[tuple[str, str], list[object]] = {}
    for row in rows:
        payload = decode(row["payload"], "search payload")
        result = decode(row["result"], "search result")
        if not isinstance(payload, dict) or not isinstance(result, dict):
            fail("search operation evidence has the wrong shape")
        key = (payload.get("description"), payload.get("city"))
        if key not in EXPECTED_SEARCHES:
            fail(f"unexpected or incorrectly scoped search: {key!r}")
        matches = result.get("matches")
        if not isinstance(matches, list):
            fail(f"search for {key!r} did not return a match list")
        matches_by_search[key] = matches
    if set(matches_by_search) != set(EXPECTED_SEARCHES):
        fail("both exact description-and-city searches are required")
    latest_start = max(row["started_ns"] for row in rows)
    earliest_finish = min(row["finished_ns"] for row in rows)
    if latest_start >= earliest_finish:
        fail("the two independent searches did not overlap")
    return matches_by_search


def verify_searches(rows: list[sqlite3.Row]) -> dict[tuple[str, str], str]:
    matches_by_search = parse_searches(rows)
    resolved: dict[tuple[str, str], str] = {}
    for key, matches in matches_by_search.items():
        if len(matches) != 1:
            fail(f"search for {key!r} did not produce one unique match")
        match = matches[0]
        if not isinstance(match, dict) or match.get("id") != EXPECTED_SEARCHES[key]:
            fail(f"search for {key!r} did not resolve the correct stable ID")
        resolved[key] = match["id"]
    return resolved


def parse_gets(
    rows: list[sqlite3.Row], searches: list[sqlite3.Row], discovered: set[str]
) -> dict[str, object]:
    if len(rows) != 2:
        fail(f"expected exactly two complete-record retrievals, found {len(rows)}")
    latest_search_finish = max(row["finished_ns"] for row in searches)
    if min(row["started_ns"] for row in rows) < latest_search_finish:
        fail("a retrieval began before both searches finished")

    returned: dict[str, object] = {}
    for row in rows:
        payload = decode(row["payload"], "get payload")
        result = decode(row["result"], "get result")
        if not isinstance(payload, dict) or not isinstance(result, dict):
            fail("retrieval operation evidence has the wrong shape")
        record_id = payload.get("id")
        if record_id not in discovered:
            fail("a retrieval used an ID not discovered by the required searches")
        if record_id in returned:
            fail("a searched record was retrieved more than once")
        returned[record_id] = result.get("record")
    if set(returned) != discovered:
        fail("both searched records must be retrieved exactly once")

    latest_start = max(row["started_ns"] for row in rows)
    earliest_finish = min(row["finished_ns"] for row in rows)
    if latest_start >= earliest_finish:
        fail("the two dependent retrievals did not overlap")
    return returned


def verify_gets(
    rows: list[sqlite3.Row], searches: list[sqlite3.Row], discovered: set[str]
) -> None:
    returned = parse_gets(rows, searches, discovered)
    for record_id, record in returned.items():
        if not isinstance(record, dict) or record.get("id") != record_id:
            fail("a retrieval did not return its requested complete record")
        expected = BASE_RECORDS[record_id]
        if (
            record.get("description") != expected[0]
            or record.get("city") != expected[1]
            or not isinstance(record.get("status"), str)
            or not isinstance(record.get("expense_date"), str)
            or not isinstance(record.get("amount"), str)
            or not isinstance(record.get("submitted_by"), str)
        ):
            fail("a complete record was missing or mismatched required fields")


def verify_search_abort(
    scenario: str, matches_by_search: dict[tuple[str, str], list[object]]
) -> None:
    airport = matches_by_search[("Airport shuttle receipt", "Boston")]
    workshop = matches_by_search[("Workshop parking receipt", "Denver")]
    if scenario == "ambiguous-search":
        workshop_ids = {
            match.get("id") for match in workshop if isinstance(match, dict)
        }
        if (
            len(airport) != 1
            or not isinstance(airport[0], dict)
            or airport[0].get("id") != AIRPORT_ID
            or len(workshop) != 2
            or len(workshop_ids) != 2
            or WORKSHOP_ID not in workshop_ids
            or not all(
                isinstance(record_id, str) and record_id
                for record_id in workshop_ids
            )
        ):
            fail("the ambiguous-search fixture did not return the intended results")
    elif scenario == "unresolved-search":
        if (
            airport
            or len(workshop) != 1
            or not isinstance(workshop[0], dict)
            or workshop[0].get("id") != WORKSHOP_ID
        ):
            fail("the unresolved-search fixture did not return the intended results")
    elif scenario == "missing-search-id":
        if (
            len(airport) != 1
            or not isinstance(airport[0], dict)
            or airport[0].get("id") != AIRPORT_ID
            or len(workshop) != 1
            or not isinstance(workshop[0], dict)
            or workshop[0].get("id") != ""
        ):
            fail("the missing-search-id fixture did not return the intended results")
    else:
        fail(f"unknown search-abort scenario: {scenario}")


def verify_retrieval_abort(
    scenario: str, returned: dict[str, object]
) -> None:
    airport = returned.get(AIRPORT_ID)
    if not isinstance(airport, dict) or airport.get("id") != AIRPORT_ID:
        fail("the retrieval-abort fixture corrupted the unrelated airport record")
    workshop = returned.get(WORKSHOP_ID)
    if scenario == "missing-record":
        if workshop is not None:
            fail("the missing-record fixture unexpectedly returned the target")
    elif scenario == "mismatched-record":
        if (
            not isinstance(workshop, dict)
            or workshop.get("id") != WORKSHOP_ID
            or workshop.get("description") != "Workshop parking receipt"
            or workshop.get("city") != "Boulder"
        ):
            fail("the mismatched-record fixture did not return the intended mismatch")
    elif scenario == "missing-status":
        if (
            not isinstance(workshop, dict)
            or workshop.get("id") != WORKSHOP_ID
            or "status" in workshop
        ):
            fail("the missing-status fixture unexpectedly returned a target status")
    else:
        fail(f"unknown retrieval-abort scenario: {scenario}")


def verify_case(scenario: str) -> None:
    search_abort = scenario in {
        "ambiguous-search",
        "unresolved-search",
        "missing-search-id",
    }
    retrieval_abort = scenario in {
        "missing-record",
        "mismatched-record",
        "missing-status",
    }
    with tempfile.TemporaryDirectory(prefix="pi-expense-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)

        database = state / "expenses.sqlite3"
        if not database.is_file():
            fail("could not initialize isolated expense state")
        before_db = sqlite3.connect(database)
        before_db.row_factory = sqlite3.Row
        before_records = records_snapshot(before_db)
        before_db.close()

        env = os.environ.copy()
        for name in tuple(env):
            if name.startswith("PI_EXPENSE_"):
                env.pop(name)
        env["PI_EXPENSE_STATE_DIR"] = str(state)
        env["TMPDIR"] = str(scratch)
        try:
            process = subprocess.run(
                [str(SOLUTION)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail(f"reconcile_expenses.sh timed out in {scenario} scenario")
        if not search_abort and not retrieval_abort and process.returncode != 0:
            fail(f"reconcile_expenses.sh failed in {scenario} scenario", process)
        if any(scratch.iterdir()):
            fail("reconcile_expenses.sh left temporary files behind")

        db = sqlite3.connect(database)
        db.row_factory = sqlite3.Row
        operations = db.execute(
            "SELECT * FROM operations ORDER BY started_ns, invocation_id"
        ).fetchall()
        if any(row["finished_ns"] is None for row in operations):
            fail("an expense operation did not finish")
        searches = [row for row in operations if row["command"] == "search"]
        gets = [row for row in operations if row["command"] == "get"]
        updates = [row for row in operations if row["command"] == "update"]
        prohibited = [
            row
            for row in operations
            if row["command"] in {"create", "delete", "notify"}
        ]
        if prohibited:
            fail("a prohibited create, delete, or notification operation was used")

        if search_abort:
            if len(operations) != 2 or gets or updates:
                fail("an unresolved search must stop every retrieval and write")
            matches_by_search = parse_searches(searches)
            verify_search_abort(scenario, matches_by_search)
            expected_records = before_records
        elif retrieval_abort:
            if len(operations) != 4 or updates:
                fail("an invalid complete record must stop every write")
            resolved = verify_searches(searches)
            discovered = set(resolved.values())
            returned = parse_gets(gets, searches, discovered)
            verify_retrieval_abort(scenario, returned)
            expected_records = before_records
        else:
            if len(operations) != 4 + len(updates):
                fail("unexpected extra expense operations were used")
            resolved = verify_searches(searches)
            discovered = set(resolved.values())
            verify_gets(gets, searches, discovered)
            latest_get_finish = max(row["finished_ns"] for row in gets)

            expected_records = dict(before_records)
            if scenario == "submitted":
                if len(updates) != 1:
                    fail("the submitted target required exactly one conditional update")
                update = updates[0]
                if update["started_ns"] < latest_get_finish:
                    fail("the update began before both complete records were retrieved")
                payload = decode(update["payload"], "update payload")
                result = decode(update["result"], "update result")
                if payload != {"id": WORKSHOP_ID, "status": "needs-receipt"}:
                    fail("the conditional update targeted the wrong record or status")
                if not isinstance(result, dict) or (
                    result.get("updated") != 1
                    or result.get("before_status") != "submitted"
                    or not isinstance(result.get("record"), dict)
                    or result["record"].get("status") != "needs-receipt"
                ):
                    fail("the conditional update result was not verified")
                target = list(expected_records[WORKSHOP_ID])
                target[2] = "needs-receipt"
                expected_records[WORKSHOP_ID] = tuple(target)
            elif scenario == "already-reviewed":
                if updates:
                    fail("the non-submitted target must not be updated")
            else:
                fail(f"unknown success scenario: {scenario}")

        if records_snapshot(db) != expected_records:
            fail("a protected or unrelated expense record was changed")
        notification_count = db.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        if notification_count != 0:
            fail("a notification was created")
        db.close()


def verify() -> None:
    if not SOLUTION.is_file():
        fail("reconcile_expenses.sh is missing")
    if not os.access(SOLUTION, os.X_OK):
        fail("reconcile_expenses.sh is not executable")
    source = SOLUTION.read_text(encoding="utf-8", errors="replace")
    prohibited_source_markers = (
        "exp-7c4ae1",
        "exp-b29d63",
        "PI_EXPENSE_",
        "expenses.sqlite3",
        "sqlite3",
        ".harness",
        ".expense_runtime",
    )
    if any(marker in source for marker in prohibited_source_markers):
        fail("the solution bypasses discovery or accesses protected runtime state")
    verify_case("submitted")
    verify_case("already-reviewed")
    verify_case("ambiguous-search")
    verify_case("unresolved-search")
    verify_case("missing-search-id")
    verify_case("missing-record")
    verify_case("mismatched-record")
    verify_case("missing-status")
    print(
        "PASS: parallel searches, dependent parallel retrievals, conditional update, "
        "no-change and abort branches, and negative constraints verified"
    )


if __name__ == "__main__":
    verify()
