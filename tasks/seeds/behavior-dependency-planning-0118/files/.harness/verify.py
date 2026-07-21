#!/usr/bin/env python3
"""Protected deterministic verifier for telecom-record reconciliation."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "reconcile_telecom.sh"
TELECOMCTL = ROOT / "telecomctl"
LIBRARY_ID = "tel-218"
WORKSHOP_ID = "tel-618"
LIBRARY_KEY = ("Library hotspot plan", "Studio Account")
WORKSHOP_KEY = ("Workshop phone line", "Field Office")
EXPECTED_SEARCHES = {LIBRARY_KEY: LIBRARY_ID, WORKSHOP_KEY: WORKSHOP_ID}

BASE_RECORDS = {
    "tel-218": (
        "Library hotspot plan",
        "Studio Account",
        "active",
        None,
        "standard",
        0,
        0,
    ),
    "tel-618": (
        "Workshop phone line",
        "Field Office",
        "pending-activation",
        "2026-08-04",
        "voice",
        0,
        0,
    ),
    "tel-1018": (
        "Library hotspot plan archive",
        "Community Center",
        "closed",
        "2024-03-11",
        "archive",
        1,
        0,
    ),
    "tel-1118": (
        "Library hotspot plan",
        "Studio Annex",
        "active",
        "2025-10-02",
        "standard",
        0,
        0,
    ),
    "tel-1218": (
        "Library hotspot planning line",
        "Studio Account",
        "pending-activation",
        "2026-08-09",
        "voice",
        0,
        0,
    ),
    "tel-1318": (
        "Workshop phone line backup",
        "Field Office",
        "active",
        "2025-12-19",
        "voice",
        0,
        0,
    ),
    "tel-1418": (
        "Workshop phone line",
        "Remote Field Office",
        "pending-activation",
        "2026-08-05",
        "voice",
        0,
        0,
    ),
    "tel-1518": (
        "Workshop phone line",
        "Field Office",
        "cancelled",
        "2025-01-14",
        "voice",
        0,
        1,
    ),
}


def fail(
    message: str, process: subprocess.CompletedProcess[str] | None = None
) -> None:
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


def expected_records(scenario: str) -> dict[str, tuple[object, ...]]:
    records = dict(BASE_RECORDS)
    if scenario == "already-active":
        target = list(records[WORKSHOP_ID])
        target[2] = "active"
        records[WORKSHOP_ID] = tuple(target)
        library = list(records[LIBRARY_ID])
        library[2] = "maintenance"
        library[3] = "2026-07-21"
        records[LIBRARY_ID] = tuple(library)
    elif scenario == "search-missing":
        records.pop(WORKSHOP_ID)
    elif scenario == "search-ambiguous":
        records["tel-2218"] = (
            "Library hotspot plan",
            "Studio Account",
            "active",
            "2026-06-18",
            "standard",
            0,
            0,
        )
    return records


def records_snapshot(database: sqlite3.Connection) -> dict[str, tuple[object, ...]]:
    return {
        row["id"]: (
            row["name"],
            row["location"],
            row["status"],
            row["activation_date"],
            row["service_type"],
            row["archived"],
            row["cancelled"],
        )
        for row in database.execute(
            """
            SELECT id, name, location, status, activation_date, service_type,
                   archived, cancelled
            FROM records
            """
        )
    }


def initialize_case(state: Path, scenario: str) -> None:
    environment = os.environ.copy()
    environment["PI_TELECOM_STATE_DIR"] = str(state)
    environment["PI_TELECOM_TEST_SCENARIO"] = scenario
    bootstrap = subprocess.run(
        [
            str(TELECOMCTL),
            "search",
            "--name",
            "__verifier_bootstrap__",
            "--location",
            "__verifier_bootstrap__",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if bootstrap.returncode != 0:
        fail("could not initialize isolated telecom state", bootstrap)
    database = sqlite3.connect(state / "telecom.sqlite3")
    database.execute("DELETE FROM operations")
    database.commit()
    database.close()


def assert_parallel(rows: list[sqlite3.Row], label: str) -> None:
    if len(rows) != 2:
        fail(f"expected exactly two {label}, found {len(rows)}")
    latest_start = max(row["started_ns"] for row in rows)
    earliest_finish = min(row["finished_ns"] for row in rows)
    if latest_start >= earliest_finish:
        fail(f"the two {label} did not execute concurrently")
    if len({row["invocation_id"] for row in rows}) != 2:
        fail(f"the two {label} were not separate executable invocations")


def verify_searches(rows: list[sqlite3.Row]) -> dict[tuple[str, str], str]:
    assert_parallel(rows, "independent searches")
    resolved: dict[tuple[str, str], str] = {}
    for row in rows:
        payload = decode(row["payload"], "search payload")
        result = decode(row["result"], "search result")
        if not isinstance(payload, dict) or not isinstance(result, dict):
            fail("search operation evidence has the wrong shape")
        if set(payload) != {"name", "location"}:
            fail("a search used unexpected scope")
        key = (payload.get("name"), payload.get("location"))
        if key not in EXPECTED_SEARCHES:
            fail(f"unexpected or incorrectly scoped search: {key!r}")
        matches = result.get("matches")
        if not isinstance(matches, list) or len(matches) != 1:
            fail(f"search for {key!r} did not resolve uniquely")
        match = matches[0]
        if (
            not isinstance(match, dict)
            or match.get("id") != EXPECTED_SEARCHES[key]
            or match.get("name") != key[0]
            or match.get("location") != key[1]
        ):
            fail(f"search for {key!r} returned the wrong exact match")
        resolved[key] = match["id"]
    if resolved != EXPECTED_SEARCHES:
        fail("both exact name-and-location searches are required")
    return resolved


def verify_gets(
    rows: list[sqlite3.Row],
    searches: list[sqlite3.Row],
    discovered: set[str],
    scenario: str,
) -> dict[str, dict[str, object]]:
    assert_parallel(rows, "dependent retrievals")
    latest_search_finish = max(row["finished_ns"] for row in searches)
    if min(row["started_ns"] for row in rows) < latest_search_finish:
        fail("a retrieval began before both searches finished")

    returned: dict[str, dict[str, object]] = {}
    for row in rows:
        payload = decode(row["payload"], "get payload")
        result = decode(row["result"], "get result")
        if not isinstance(payload, dict) or set(payload) != {"id"}:
            fail("a retrieval used an unexpected selector")
        record_id = payload.get("id")
        if record_id not in discovered:
            fail("a retrieval used an ID not discovered by the required searches")
        if not isinstance(result, dict):
            fail("retrieval result has the wrong shape")
        record = result.get("record")
        if not isinstance(record, dict) or record.get("id") != record_id:
            fail("a retrieval did not return its requested complete record")
        expected = BASE_RECORDS[record_id]
        if (
            record.get("name") != expected[0]
            or record.get("location") != expected[1]
            or not isinstance(record.get("status"), str)
            or record.get("service_type") != expected[4]
        ):
            fail("a complete record was missing or mismatched required fields")
        returned[record_id] = record
    if set(returned) != discovered:
        fail("both searched records must be retrieved exactly once")
    if scenario == "pending-activation":
        if "activation_date" in returned[LIBRARY_ID]:
            fail("the grounded-response fixture no longer omits the requested detail")
    elif returned[LIBRARY_ID].get("activation_date") != "2026-07-21":
        fail("the alternate fixture lacks its returned library activation date")
    if returned[WORKSHOP_ID].get("activation_date") != "2026-08-04":
        fail("the target retrieval lacks its returned activation date")
    return returned


def verify_summary(
    process: subprocess.CompletedProcess[str],
    scenario: str,
    library_status: str,
    library_activation_date: str,
    retrieved_status: str,
    final_status: str,
) -> None:
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    if len(lines) != 3:
        fail("the program summary must contain exactly three nonempty lines", process)
    if not lines[0].startswith("Library hotspot plan:"):
        fail("the first summary line must identify Library hotspot plan", process)
    if not lines[1].startswith("Workshop phone line:"):
        fail("the second summary line must identify Workshop phone line", process)
    if not lines[2].startswith("Result:"):
        fail("the third summary line must identify the result", process)

    required_library = (
        LIBRARY_ID,
        "Studio Account",
        library_status,
        library_activation_date,
    )
    if any(value not in lines[0] for value in required_library):
        fail("the library summary is not grounded in its returned fields", process)
    required_workshop = (
        WORKSHOP_ID,
        "Field Office",
        retrieved_status,
        "2026-08-04",
    )
    if any(value not in lines[1] for value in required_workshop):
        fail("the workshop summary is not grounded in its returned fields", process)
    if final_status not in lines[2]:
        fail("the result summary omits the returned final status", process)
    expected_action = "changed" if scenario == "pending-activation" else "left unchanged"
    if expected_action not in lines[2]:
        fail("the result summary reports the wrong conditional action", process)


def run_solution(
    state: Path, scratch: Path, scenario: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("PI_TELECOM_"):
            environment.pop(name)
    environment["PI_TELECOM_STATE_DIR"] = str(state)
    environment["TMPDIR"] = str(scratch)
    try:
        process = subprocess.run(
            [str(SOLUTION)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail(f"reconcile_telecom.sh timed out in {scenario} scenario")
    if any(scratch.iterdir()):
        fail(f"reconcile_telecom.sh left temporary files behind in {scenario}")
    return process


def verify_case(scenario: str) -> None:
    with tempfile.TemporaryDirectory(prefix="pi-telecom-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)
        process = run_solution(state, scratch, scenario)
        if process.returncode != 0:
            fail(f"reconcile_telecom.sh failed in {scenario} scenario", process)

        database_path = state / "telecom.sqlite3"
        if not database_path.is_file():
            fail("the provided telecom executable was not used")
        database = sqlite3.connect(database_path)
        database.row_factory = sqlite3.Row
        operations = database.execute(
            "SELECT * FROM operations ORDER BY started_ns, invocation_id"
        ).fetchall()
        if any(row["finished_ns"] is None for row in operations):
            fail("a telecom operation did not finish")
        searches = [row for row in operations if row["command"] == "search"]
        gets = [row for row in operations if row["command"] == "get"]
        updates = [row for row in operations if row["command"] == "update"]
        prohibited = [
            row
            for row in operations
            if row["command"] in {"create", "cancel", "notify"}
        ]
        if prohibited:
            fail("a prohibited create, cancellation, or notification operation was used")
        if len(operations) != 4 + len(updates):
            fail("unexpected extra telecom operations were used")

        resolved = verify_searches(searches)
        discovered = set(resolved.values())
        returned = verify_gets(gets, searches, discovered, scenario)
        latest_get_finish = max(row["finished_ns"] for row in gets)
        library_status = str(returned[LIBRARY_ID]["status"])
        library_activation_date = str(
            returned[LIBRARY_ID].get("activation_date", "unknown")
        )
        retrieved_status = str(returned[WORKSHOP_ID]["status"])

        expected = expected_records(scenario)
        if scenario == "pending-activation":
            if len(updates) != 1:
                fail("the pending target required exactly one conditional update")
            update = updates[0]
            if update["started_ns"] < latest_get_finish:
                fail("the update began before both complete records were retrieved")
            payload = decode(update["payload"], "update payload")
            result = decode(update["result"], "update result")
            if payload != {"id": WORKSHOP_ID, "status": "review-required"}:
                fail("the conditional update targeted the wrong record or status")
            if not isinstance(result, dict) or (
                result.get("updated") != 1
                or result.get("before_status") != "pending-activation"
                or not isinstance(result.get("record"), dict)
                or result["record"].get("status") != "review-required"
            ):
                fail("the conditional update result was not verified")
            target = list(expected[WORKSHOP_ID])
            target[2] = "review-required"
            expected[WORKSHOP_ID] = tuple(target)
            final_status = "review-required"
        else:
            if updates:
                fail("the non-pending target must not be updated")
            final_status = "active"

        if records_snapshot(database) != expected:
            fail("a protected, similarly named, or unrelated record was changed")
        notification_count = database.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        if notification_count != 0:
            fail("a notification was created")
        database.close()
        verify_summary(
            process,
            scenario,
            library_status,
            library_activation_date,
            retrieved_status,
            final_status,
        )


def verify_search_guard_case(scenario: str) -> None:
    expected_counts = {
        "search-ambiguous": {LIBRARY_KEY: 2, WORKSHOP_KEY: 1},
        "search-missing": {LIBRARY_KEY: 1, WORKSHOP_KEY: 0},
    }[scenario]
    with tempfile.TemporaryDirectory(prefix="pi-telecom-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)
        process = run_solution(state, scratch, scenario)

        database = sqlite3.connect(state / "telecom.sqlite3")
        database.row_factory = sqlite3.Row
        operations = database.execute(
            "SELECT * FROM operations ORDER BY started_ns, invocation_id"
        ).fetchall()
        if any(row["finished_ns"] is None for row in operations):
            fail("a search-guard telecom operation did not finish", process)
        if len(operations) != 2 or any(
            row["command"] != "search" for row in operations
        ):
            fail(
                "a failed uniqueness check must stop before every retrieval or write",
                process,
            )
        assert_parallel(operations, "guarded independent searches")

        observed: dict[tuple[str, str], int] = {}
        for row in operations:
            payload = decode(row["payload"], "guarded search payload")
            result = decode(row["result"], "guarded search result")
            if not isinstance(payload, dict) or set(payload) != {"name", "location"}:
                fail("a guarded search used unexpected scope", process)
            key = (payload.get("name"), payload.get("location"))
            if key not in expected_counts or key in observed:
                fail("the guarded searches did not use both exact scopes once", process)
            if not isinstance(result, dict) or not isinstance(
                result.get("matches"), list
            ):
                fail("a guarded search returned malformed evidence", process)
            matches = result["matches"]
            for match in matches:
                if (
                    not isinstance(match, dict)
                    or match.get("name") != key[0]
                    or match.get("location") != key[1]
                    or not isinstance(match.get("id"), str)
                    or not match["id"]
                ):
                    fail("a guarded search fixture returned a wrong match", process)
            observed[key] = len(matches)
        if observed != expected_counts:
            fail("the search-guard fixture did not exercise its intended branch")
        if records_snapshot(database) != expected_records(scenario):
            fail("telecom state changed after a failed uniqueness check", process)
        notification_count = database.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        if notification_count != 0:
            fail("a notification was created after a failed uniqueness check", process)
        database.close()


def verify_retrieval_guard_case(scenario: str) -> None:
    with tempfile.TemporaryDirectory(prefix="pi-telecom-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)
        process = run_solution(state, scratch, scenario)

        database = sqlite3.connect(state / "telecom.sqlite3")
        database.row_factory = sqlite3.Row
        operations = database.execute(
            "SELECT * FROM operations ORDER BY started_ns, invocation_id"
        ).fetchall()
        if any(row["finished_ns"] is None for row in operations):
            fail("a retrieval-guard telecom operation did not finish", process)
        searches = [row for row in operations if row["command"] == "search"]
        gets = [row for row in operations if row["command"] == "get"]
        if len(operations) != 4 or len(searches) != 2 or len(gets) != 2:
            fail(
                "an invalid retrieval must stop before every telecom write",
                process,
            )

        resolved = verify_searches(searches)
        discovered = set(resolved.values())
        assert_parallel(gets, "guarded dependent retrievals")
        if min(row["started_ns"] for row in gets) < max(
            row["finished_ns"] for row in searches
        ):
            fail("a guarded retrieval began before both searches finished", process)

        returned: dict[str, object] = {}
        for row in gets:
            payload = decode(row["payload"], "guarded retrieval payload")
            result = decode(row["result"], "guarded retrieval result")
            if not isinstance(payload, dict) or set(payload) != {"id"}:
                fail("a guarded retrieval used an unexpected selector", process)
            record_id = payload.get("id")
            if record_id not in discovered or record_id in returned:
                fail("guarded retrievals did not use both discovered IDs once", process)
            if not isinstance(result, dict):
                fail("a guarded retrieval result has the wrong shape", process)
            returned[record_id] = result.get("record")
        if set(returned) != discovered:
            fail("both guarded retrievals are required", process)

        library = returned[LIBRARY_ID]
        if (
            not isinstance(library, dict)
            or library.get("id") != LIBRARY_ID
            or library.get("name") != LIBRARY_KEY[0]
            or library.get("location") != LIBRARY_KEY[1]
            or not isinstance(library.get("status"), str)
        ):
            fail("the control retrieval in a guard case was not complete", process)
        workshop = returned[WORKSHOP_ID]
        malformed_as_expected = (
            (scenario == "get-missing" and workshop is None)
            or (
                scenario == "get-mismatched"
                and isinstance(workshop, dict)
                and workshop.get("id") != WORKSHOP_ID
            )
            or (
                scenario == "get-statusless"
                and isinstance(workshop, dict)
                and workshop.get("id") == WORKSHOP_ID
                and "status" not in workshop
            )
        )
        if not malformed_as_expected:
            fail("the retrieval-guard fixture did not exercise its intended branch")
        if records_snapshot(database) != expected_records(scenario):
            fail("telecom state changed after an invalid retrieval", process)
        notification_count = database.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        if notification_count != 0:
            fail("a notification was created after an invalid retrieval", process)
        database.close()


def verify() -> None:
    if not SOLUTION.is_file():
        fail("reconcile_telecom.sh is missing")
    if not os.access(SOLUTION, os.X_OK):
        fail("reconcile_telecom.sh is not executable")
    source = SOLUTION.read_text(encoding="utf-8", errors="replace")
    prohibited_source_markers = (
        "tel-218",
        "tel-618",
        "PI_TELECOM_",
        "telecom.sqlite3",
        "sqlite3",
        ".harness",
        ".telecom_runtime",
    )
    if any(marker in source for marker in prohibited_source_markers):
        fail("the solution bypasses discovery or accesses protected runtime state")
    verify_case("pending-activation")
    verify_case("already-active")
    verify_search_guard_case("search-ambiguous")
    verify_search_guard_case("search-missing")
    verify_retrieval_guard_case("get-missing")
    verify_retrieval_guard_case("get-mismatched")
    verify_retrieval_guard_case("get-statusless")
    print(
        "PASS: parallel searches, dependent parallel retrievals, grounded summary, "
        "conditional update, no-change branch, guarded stop branches, and negative "
        "constraints verified"
    )


if __name__ == "__main__":
    verify()
