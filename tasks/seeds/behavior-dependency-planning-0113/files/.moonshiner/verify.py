#!/usr/bin/env python3
"""Protected deterministic verifier for the library reconciliation task."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "reconcile_library.sh"
LIBRARY = ROOT / "library"
TARGET_ID = "bk-c91e76"
URBAN_ID = "bk-7b42d1"
EXPECTED_SEARCHES = {
    ("Urban Orchard Handbook", "East Branch"): URBAN_ID,
    ("Poems from the High Desert", "Riverside Branch"): TARGET_ID,
}
BASE_RECORDS = {
    "bk-7b42d1": (
        "Urban Orchard Handbook",
        "East Branch",
        "available",
        "Mara Velasquez",
        0,
    ),
    "bk-c91e76": (
        "Poems from the High Desert",
        "Riverside Branch",
        "on-loan",
        "Tomas Ibarra",
        0,
    ),
    "bk-2ee830": (
        "Urban Orchard Handbook",
        "West Branch",
        "on-loan",
        "Mara Velasquez",
        0,
    ),
    "bk-4ad0f5": (
        "Urban Orchard Handbook: Field Notes",
        "East Branch",
        "available",
        "Mara Velasquez",
        0,
    ),
    "bk-890c3a": (
        "Urban Orchard Handbook archive",
        "Bookmobile",
        "closed",
        "Mara Velasquez",
        0,
    ),
    "bk-63fb27": (
        "Poems from the High Desert — Annotated",
        "Riverside Branch",
        "available",
        "Tomas Ibarra",
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
            row["title"],
            row["branch"],
            row["status"],
            row["author"],
            row["deleted"],
        )
        for row in db.execute(
            "SELECT id, title, branch, status, author, deleted FROM records"
        )
    }


def initialize_case(state: Path, scenario: str) -> None:
    env = os.environ.copy()
    env["PI_LIBRARY_STATE_DIR"] = str(state)
    if scenario != "on-loan":
        env["PI_LIBRARY_TEST_SCENARIO"] = scenario
    bootstrap = subprocess.run(
        [
            str(LIBRARY),
            "search",
            "--title",
            "__verifier_bootstrap__",
            "--branch",
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
        fail("could not initialize isolated library state", bootstrap)
    db = sqlite3.connect(state / "library.sqlite3")
    db.execute("DELETE FROM operations")
    db.execute("DELETE FROM operation_events")
    db.execute("DELETE FROM parallel_barriers")
    db.commit()
    db.close()


def event_sequence(
    db: sqlite3.Connection, row: sqlite3.Row, phase: str
) -> int:
    events = db.execute(
        "SELECT sequence FROM operation_events "
        "WHERE invocation_id = ? AND phase = ?",
        (row["invocation_id"], phase),
    ).fetchall()
    if len(events) != 1:
        fail(f"operation has invalid {phase} event evidence")
    return events[0]["sequence"]


def verify_parallel_barrier(
    db: sqlite3.Connection, rows: list[sqlite3.Row], command: str
) -> None:
    arrivals = db.execute(
        "SELECT invocation_id FROM parallel_barriers WHERE command = ?", (command,)
    ).fetchall()
    expected = {row["invocation_id"] for row in rows}
    observed = {row["invocation_id"] for row in arrivals}
    if len(arrivals) != 2 or observed != expected:
        fail(f"the two {command} operations did not rendezvous as parallel processes")


def verify_searches(
    db: sqlite3.Connection, rows: list[sqlite3.Row]
) -> dict[tuple[str, str], str]:
    if len(rows) != 2:
        fail(f"expected exactly two searches, found {len(rows)}")
    resolved: dict[tuple[str, str], str] = {}
    for row in rows:
        payload = decode(row["payload"], "search payload")
        result = decode(row["result"], "search result")
        if not isinstance(payload, dict) or not isinstance(result, dict):
            fail("search audit data has the wrong shape")
        key = (payload.get("title"), payload.get("branch"))
        if key not in EXPECTED_SEARCHES:
            fail(f"unexpected or incorrectly scoped search: {key!r}")
        matches = result.get("matches")
        if not isinstance(matches, list) or len(matches) != 1:
            fail(f"search for {key!r} did not produce one unique match")
        match = matches[0]
        if not isinstance(match, dict) or match.get("id") != EXPECTED_SEARCHES[key]:
            fail(f"search for {key!r} did not resolve the correct stable ID")
        resolved[key] = match["id"]
    if resolved != EXPECTED_SEARCHES:
        fail("both exact title-and-branch searches are required")
    verify_parallel_barrier(db, rows, "search")
    return resolved


def verify_gets(
    db: sqlite3.Connection,
    rows: list[sqlite3.Row],
    searches: list[sqlite3.Row],
) -> None:
    if len(rows) != 2:
        fail(f"expected exactly two complete-record retrievals, found {len(rows)}")
    latest_search_finish = max(event_sequence(db, row, "finished") for row in searches)
    earliest_get_start = min(event_sequence(db, row, "started") for row in rows)
    if earliest_get_start < latest_search_finish:
        fail("a retrieval began before both searches finished")

    returned_ids: set[str] = set()
    for row in rows:
        payload = decode(row["payload"], "get payload")
        result = decode(row["result"], "get result")
        if not isinstance(payload, dict) or not isinstance(result, dict):
            fail("retrieval audit data has the wrong shape")
        record_id = payload.get("id")
        if record_id not in {URBAN_ID, TARGET_ID}:
            fail("a retrieval used an ID not discovered by the required searches")
        record = result.get("record")
        if not isinstance(record, dict) or record.get("id") != record_id:
            fail("a retrieval did not return its requested complete record")
        expected_title = BASE_RECORDS[record_id][0]
        expected_branch = BASE_RECORDS[record_id][1]
        status = record.get("status")
        if (
            record.get("title") != expected_title
            or record.get("branch") != expected_branch
        ):
            fail("a complete record was missing or mismatched required fields")
        if not isinstance(status, str) or not status:
            fail("a complete record lacked a status")
        returned_ids.add(record_id)
    if returned_ids != {URBAN_ID, TARGET_ID}:
        fail("both searched records must be retrieved exactly once")
    verify_parallel_barrier(db, rows, "get")


def run_solution(env: dict[str, str], scenario: str) -> subprocess.CompletedProcess[str]:
    command = [str(SOLUTION)]
    child = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = child.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        stdout, stderr = child.communicate()
        process = subprocess.CompletedProcess(
            command, child.returncode, stdout=stdout, stderr=stderr
        )
        fail(f"reconcile_library.sh timed out in {scenario} scenario", process)
    return subprocess.CompletedProcess(
        command, child.returncode, stdout=stdout, stderr=stderr
    )


def verify_case(scenario: str) -> None:
    with tempfile.TemporaryDirectory(prefix="pi-library-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)

        env = os.environ.copy()
        for name in tuple(env):
            if name.startswith("PI_LIBRARY_"):
                env.pop(name)
        env["PI_LIBRARY_STATE_DIR"] = str(state)
        env["PI_LIBRARY_TEST_SYNC"] = "1"
        env["TMPDIR"] = str(scratch)
        process = run_solution(env, scenario)
        if process.returncode != 0:
            fail(f"reconcile_library.sh failed in {scenario} scenario", process)
        if any(scratch.iterdir()):
            fail("reconcile_library.sh left temporary files behind")

        database = state / "library.sqlite3"
        if not database.is_file():
            fail("the provided library executable was not used")
        db = sqlite3.connect(database)
        db.row_factory = sqlite3.Row
        operations = db.execute(
            "SELECT * FROM operations ORDER BY invocation_id"
        ).fetchall()
        if any(row["finished_ns"] is None for row in operations):
            fail("a library operation did not finish")
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
        if len(operations) != 4 + len(updates):
            fail("unexpected extra library operations were used")

        verify_searches(db, searches)
        verify_gets(db, gets, searches)
        latest_get_finish = max(event_sequence(db, row, "finished") for row in gets)

        expected_records = dict(BASE_RECORDS)
        if scenario == "on-loan":
            if len(updates) != 1:
                fail("the on-loan target required exactly one conditional update")
            update = updates[0]
            if event_sequence(db, update, "started") < latest_get_finish:
                fail("the update began before both complete records were retrieved")
            payload = decode(update["payload"], "update payload")
            result = decode(update["result"], "update result")
            if payload != {"id": TARGET_ID, "status": "on-hold"}:
                fail("the conditional update targeted the wrong record or status")
            if not isinstance(result, dict) or (
                result.get("updated") != 1
                or result.get("before_status") != "on-loan"
                or not isinstance(result.get("record"), dict)
                or result["record"].get("status") != "on-hold"
            ):
                fail("the conditional update result was not verified")
            target = list(expected_records[TARGET_ID])
            target[2] = "on-hold"
            expected_records[TARGET_ID] = tuple(target)
        else:
            if updates:
                fail("the non-on-loan target must not be updated")
            target = list(expected_records[TARGET_ID])
            target[2] = {
                "already-returned": "returned",
                "already-available": "available",
            }[scenario]
            expected_records[TARGET_ID] = tuple(target)

        if records_snapshot(db) != expected_records:
            fail("a protected or unrelated library record was changed")
        notification_count = db.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        if notification_count != 0:
            fail("a notification was created")
        db.close()


def verify() -> None:
    if not SOLUTION.is_file():
        fail("reconcile_library.sh is missing")
    if not os.access(SOLUTION, os.X_OK):
        fail("reconcile_library.sh is not executable")
    source = SOLUTION.read_text(encoding="utf-8", errors="replace")
    prohibited_source_markers = (
        "bk-7b42d1",
        "bk-c91e76",
        "PI_LIBRARY_",
        "library.sqlite3",
        "sqlite3",
        ".moonshiner",
        ".pytest_cache",
    )
    if any(marker in source for marker in prohibited_source_markers):
        fail("the solution bypasses discovery or accesses protected runtime state")
    verify_case("on-loan")
    verify_case("already-returned")
    verify_case("already-available")
    print(
        "PASS: parallel searches, dependent parallel retrievals, conditional update, "
        "no-change branches, and negative constraints verified"
    )


if __name__ == "__main__":
    verify()
