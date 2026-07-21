#!/usr/bin/env python3
"""Protected deterministic verifier for the clinic reconciliation task."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "reconcile_clinic.sh"
CLINICCTL = ROOT / "clinicctl"
ANNUAL_ID = "hea-215"
EYE_ID = "hea-615"
ANNUAL_KEY = ("Annual physical — Sam Ortiz", "Dale Clinic")
EYE_KEY = ("Eye exam — Drew Kim", "Northside Center")
EXPECTED_SEARCHES = {ANNUAL_KEY: ANNUAL_ID, EYE_KEY: EYE_ID}

BASE_RECORDS = {
    "hea-215": (
        "Annual physical — Sam Ortiz",
        "Dale Clinic",
        "confirmed",
        "2026-08-12",
        "Dr. Imani Reed",
        0,
        0,
    ),
    "hea-615": (
        "Eye exam — Drew Kim",
        "Northside Center",
        "requested",
        "2026-08-19",
        "Dr. Lucía Vega",
        0,
        0,
    ),
    "hea-1015": (
        "Annual physical — Sam Ortiz archive",
        "Lakeside Clinic",
        "closed",
        "2025-08-12",
        "Dr. Imani Reed",
        1,
        0,
    ),
    "hea-315": (
        "Annual physical — Sam Ortiz",
        "Harbor Clinic",
        "requested",
        "2026-08-14",
        "Dr. Omar Bell",
        0,
        0,
    ),
    "hea-415": (
        "Annual physical — Samuel Ortiz",
        "Dale Clinic",
        "confirmed",
        "2026-08-13",
        "Dr. Imani Reed",
        0,
        0,
    ),
    "hea-715": (
        "Eye exam — Drew Kim",
        "Southside Center",
        "requested",
        "2026-08-20",
        "Dr. Lucía Vega",
        0,
        0,
    ),
    "hea-815": (
        "Eye exam — Drew Kim follow-up",
        "Northside Center",
        "confirmed",
        "2026-09-02",
        "Dr. Lucía Vega",
        0,
        0,
    ),
    "hea-915": (
        "Eye exam — Drew Kim",
        "Northside Center",
        "cancelled",
        "2025-08-19",
        "Dr. Lucía Vega",
        0,
        1,
    ),
}

EARLY_STOP_CASES = {
    "ambiguous-annual": (ANNUAL_KEY, 2),
    "ambiguous-eye": (EYE_KEY, 2),
    "missing-annual": (ANNUAL_KEY, 0),
    "missing-eye": (EYE_KEY, 0),
}

INVALID_GET_CASES = {
    "get-missing-annual": (ANNUAL_ID, "missing"),
    "get-mismatched-annual": (ANNUAL_ID, "mismatched"),
    "get-statusless-annual": (ANNUAL_ID, "statusless"),
    "get-mismatched-eye": (EYE_ID, "mismatched"),
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


def expected_records(scenario: str) -> dict[str, tuple[object, ...]]:
    records = dict(BASE_RECORDS)
    if scenario == "already-reviewed":
        target = list(records[EYE_ID])
        target[2] = "confirmed"
        records[EYE_ID] = tuple(target)
    elif scenario == "ambiguous-annual":
        records["hea-216"] = (
            "Annual physical — Sam Ortiz",
            "Dale Clinic",
            "requested",
            "2026-08-26",
            "Dr. Omar Bell",
            0,
            0,
        )
    elif scenario == "ambiguous-eye":
        records["hea-616"] = (
            "Eye exam — Drew Kim",
            "Northside Center",
            "confirmed",
            "2026-08-27",
            "Dr. Mina Shah",
            0,
            0,
        )
    elif scenario == "missing-annual":
        records.pop(ANNUAL_ID)
    elif scenario == "missing-eye":
        records.pop(EYE_ID)
    return records


def records_snapshot(database: sqlite3.Connection) -> dict[str, tuple[object, ...]]:
    return {
        row["id"]: (
            row["name"],
            row["location"],
            row["status"],
            row["appointment_date"],
            row["clinician"],
            row["archived"],
            row["cancelled"],
        )
        for row in database.execute(
            """
            SELECT id, name, location, status, appointment_date, clinician,
                   archived, cancelled
            FROM records
            """
        )
    }


def initialize_case(state: Path, scenario: str) -> None:
    environment = os.environ.copy()
    environment["PI_CLINIC_STATE_DIR"] = str(state)
    environment["PI_CLINIC_TEST_SCENARIO"] = scenario
    bootstrap = subprocess.run(
        [
            str(CLINICCTL),
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
        fail("could not initialize isolated clinic state", bootstrap)
    database = sqlite3.connect(state / "appointments.sqlite3")
    database.execute("DELETE FROM operations")
    database.commit()
    database.close()


def assert_parallel(rows: list[sqlite3.Row], label: str) -> None:
    latest_start = max(row["started_ns"] for row in rows)
    earliest_finish = min(row["finished_ns"] for row in rows)
    if latest_start >= earliest_finish:
        fail(f"the two {label} did not execute concurrently")


def verify_searches(
    rows: list[sqlite3.Row], scenario: str
) -> dict[tuple[str, str], list[str]]:
    if len(rows) != 2:
        fail(f"expected exactly two searches, found {len(rows)}")
    observed: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        payload = decode(row["payload"], "search payload")
        result = decode(row["result"], "search result")
        if not isinstance(payload, dict) or not isinstance(result, dict):
            fail("search operation evidence has the wrong shape")
        key = (payload.get("name"), payload.get("location"))
        if key not in EXPECTED_SEARCHES:
            fail(f"unexpected or incorrectly scoped search: {key!r}")
        if set(payload) != {"name", "location"}:
            fail("a search used unexpected scope")
        matches = result.get("matches")
        if not isinstance(matches, list):
            fail(f"search for {key!r} returned malformed match data")
        ids: list[str] = []
        for match in matches:
            if (
                not isinstance(match, dict)
                or match.get("name") != key[0]
                or match.get("location") != key[1]
                or not isinstance(match.get("id"), str)
                or not match["id"]
            ):
                fail(f"search for {key!r} returned an incomplete exact match")
            ids.append(match["id"])
        observed[key] = ids
    if set(observed) != set(EXPECTED_SEARCHES):
        fail("both exact name-and-location searches are required")
    assert_parallel(rows, "independent searches")

    if scenario not in EARLY_STOP_CASES:
        for key, expected_id in EXPECTED_SEARCHES.items():
            if observed[key] != [expected_id]:
                fail(f"search for {key!r} did not resolve uniquely")
    else:
        failing_key, expected_count = EARLY_STOP_CASES[scenario]
        if len(observed[failing_key]) != expected_count:
            fail(f"the verifier fixture for {scenario} is invalid")
        other_key = EYE_KEY if failing_key == ANNUAL_KEY else ANNUAL_KEY
        if observed[other_key] != [EXPECTED_SEARCHES[other_key]]:
            fail("the independently resolvable lookup fixture is invalid")
    return observed


def verify_gets(
    rows: list[sqlite3.Row],
    searches: list[sqlite3.Row],
    discovered: set[str],
    expected: dict[str, tuple[object, ...]],
) -> dict[str, str]:
    if len(rows) != 2:
        fail(f"expected exactly two complete-record retrievals, found {len(rows)}")
    latest_search_finish = max(row["finished_ns"] for row in searches)
    if min(row["started_ns"] for row in rows) < latest_search_finish:
        fail("a retrieval began before both searches finished")

    returned: dict[str, str] = {}
    for row in rows:
        payload = decode(row["payload"], "get payload")
        result = decode(row["result"], "get result")
        if not isinstance(payload, dict) or not isinstance(result, dict):
            fail("retrieval operation evidence has the wrong shape")
        if set(payload) != {"id"}:
            fail("a retrieval used an unexpected selector")
        record_id = payload.get("id")
        if record_id not in discovered:
            fail("a retrieval used an ID not discovered by its required search")
        record = result.get("record")
        if not isinstance(record, dict) or record.get("id") != record_id:
            fail("a retrieval did not return its requested complete record")
        expected_record = expected[record_id]
        if (
            record.get("name") != expected_record[0]
            or record.get("location") != expected_record[1]
            or not isinstance(record.get("status"), str)
            or record.get("appointment_date") != expected_record[3]
            or record.get("clinician") != expected_record[4]
            or record.get("archived") is not False
            or record.get("cancelled") is not False
        ):
            fail("a complete record was missing or mismatched required fields")
        returned[record_id] = record["status"]
    if set(returned) != discovered:
        fail("both searched records must be retrieved exactly once")
    assert_parallel(rows, "dependent retrievals")
    return returned


def verify_early_stop(
    scenario: str,
    process: subprocess.CompletedProcess[str],
    operations: list[sqlite3.Row],
    searches: list[sqlite3.Row],
    database: sqlite3.Connection,
) -> None:
    if len(operations) != 2:
        fail("an ambiguous lookup must stop before every dependent operation")
    failing_key, expected_count = EARLY_STOP_CASES[scenario]
    output = process.stdout.casefold()
    if (
        "ambiguous" not in output
        or failing_key[0].casefold() not in output
        or failing_key[1].casefold() not in output
        or str(expected_count) not in output
    ):
        fail("the program did not identify the unresolved lookup and match count")
    if records_snapshot(database) != expected_records(scenario):
        fail("clinic state changed after an unresolved lookup")
    verify_searches(searches, scenario)


def verify_resolved_case(
    scenario: str,
    process: subprocess.CompletedProcess[str],
    operations: list[sqlite3.Row],
    searches: list[sqlite3.Row],
    gets: list[sqlite3.Row],
    updates: list[sqlite3.Row],
    database: sqlite3.Connection,
) -> None:
    observed = verify_searches(searches, scenario)
    discovered = {ids[0] for ids in observed.values()}
    expected = expected_records(scenario)
    retrieved = verify_gets(gets, searches, discovered, expected)
    latest_get_finish = max(row["finished_ns"] for row in gets)

    output = process.stdout
    for key, record_id in EXPECTED_SEARCHES.items():
        if key[0] not in output or key[1] not in output or retrieved[record_id] not in output:
            fail("program output did not report both grounded retrieved records")

    if scenario == "requested":
        if len(updates) != 1 or len(operations) != 5:
            fail("the requested target required exactly one conditional update")
        update = updates[0]
        if update["started_ns"] < latest_get_finish:
            fail("the update began before both complete records were retrieved")
        payload = decode(update["payload"], "update payload")
        result = decode(update["result"], "update result")
        if payload != {"id": EYE_ID, "status": "needs-review"}:
            fail("the conditional update targeted the wrong record or status")
        if not isinstance(result, dict) or (
            result.get("updated") != 1
            or result.get("before_status") != "requested"
            or not isinstance(result.get("record"), dict)
            or result["record"].get("id") != EYE_ID
            or result["record"].get("status") != "needs-review"
        ):
            fail("the conditional update result was not checked successfully")
        changed = list(expected[EYE_ID])
        changed[2] = "needs-review"
        expected[EYE_ID] = tuple(changed)
        if "needs-review" not in output:
            fail("program output did not report the successful permitted update")
    else:
        if updates or len(operations) != 4:
            fail("the non-requested target must not be updated")
        if "unchanged" not in output.casefold():
            fail("program output did not report the required no-change outcome")

    if records_snapshot(database) != expected:
        fail("a protected, similar, archived, cancelled, or unrelated record changed")


def verify_invalid_get_case(
    scenario: str,
    operations: list[sqlite3.Row],
    searches: list[sqlite3.Row],
    gets: list[sqlite3.Row],
    updates: list[sqlite3.Row],
    database: sqlite3.Connection,
) -> None:
    observed = verify_searches(searches, scenario)
    discovered = {ids[0] for ids in observed.values()}
    if len(operations) != 4 or updates:
        fail("an invalid complete record must stop before every write")
    if len(gets) != 2:
        fail(f"expected exactly two complete-record retrievals, found {len(gets)}")
    latest_search_finish = max(row["finished_ns"] for row in searches)
    if min(row["started_ns"] for row in gets) < latest_search_finish:
        fail("a retrieval began before both searches finished")

    results: dict[str, object] = {}
    for row in gets:
        payload = decode(row["payload"], "get payload")
        result = decode(row["result"], "get result")
        if not isinstance(payload, dict) or set(payload) != {"id"}:
            fail("a retrieval used an unexpected selector")
        record_id = payload.get("id")
        if record_id not in discovered:
            fail("a retrieval used an ID not discovered by its required search")
        if not isinstance(result, dict):
            fail("retrieval operation evidence has the wrong shape")
        results[record_id] = result.get("record")
    if set(results) != discovered:
        fail("both searched records must be retrieved exactly once")
    assert_parallel(gets, "dependent retrievals")

    invalid_id, failure_kind = INVALID_GET_CASES[scenario]
    invalid_record = results[invalid_id]
    if failure_kind == "missing" and invalid_record is not None:
        fail("the missing-record fixture is invalid")
    expected_invalid = expected_records(scenario)[invalid_id]
    if failure_kind == "mismatched" and not (
        isinstance(invalid_record, dict)
        and (
            invalid_record.get("name") != expected_invalid[0]
            or invalid_record.get("location") != expected_invalid[1]
        )
    ):
        fail("the mismatched-record fixture is invalid")
    if failure_kind == "statusless" and not (
        isinstance(invalid_record, dict) and "status" not in invalid_record
    ):
        fail("the statusless-record fixture is invalid")
    valid_id = EYE_ID if invalid_id == ANNUAL_ID else ANNUAL_ID
    valid_record = results[valid_id]
    expected_valid = expected_records(scenario)[valid_id]
    if not isinstance(valid_record, dict) or (
        valid_record.get("id") != valid_id
        or valid_record.get("name") != expected_valid[0]
        or valid_record.get("location") != expected_valid[1]
        or valid_record.get("status") != expected_valid[2]
        or valid_record.get("appointment_date") != expected_valid[3]
        or valid_record.get("clinician") != expected_valid[4]
        or valid_record.get("archived") is not False
        or valid_record.get("cancelled") is not False
    ):
        fail("the independently valid complete-record fixture is invalid")
    if records_snapshot(database) != expected_records(scenario):
        fail("clinic state changed after an invalid complete-record retrieval")


def verify_help_event(state: Path, searches: list[sqlite3.Row]) -> None:
    if not searches:
        fail("expected the two searches after consulting clinicctl help")
    help_event_path = state / "help-events.log"
    try:
        events = [
            int(line)
            for line in help_event_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, ValueError):
        fail("clinicctl built-in help was not run")
    if not events or min(events) >= min(row["started_ns"] for row in searches):
        fail("clinicctl built-in help was not run before the searches")


def verify_case(scenario: str) -> None:
    with tempfile.TemporaryDirectory(prefix="pi-clinic-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)

        environment = os.environ.copy()
        for name in tuple(environment):
            if name.startswith("PI_CLINIC_"):
                environment.pop(name)
        environment["PI_CLINIC_STATE_DIR"] = str(state)
        environment["PI_CLINIC_TEST_SCENARIO"] = scenario
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
            fail(f"reconcile_clinic.sh timed out in {scenario} scenario")
        if process.returncode != 0 and scenario not in INVALID_GET_CASES:
            fail(f"reconcile_clinic.sh failed in {scenario} scenario", process)
        if process.returncode < 0:
            fail(f"reconcile_clinic.sh was terminated in {scenario} scenario", process)
        if any(scratch.iterdir()):
            fail("reconcile_clinic.sh left temporary files behind")

        database_path = state / "appointments.sqlite3"
        if not database_path.is_file():
            fail("the provided clinicctl executable was not used")
        database = sqlite3.connect(database_path)
        database.row_factory = sqlite3.Row
        operations = database.execute(
            "SELECT * FROM operations ORDER BY started_ns, invocation_id"
        ).fetchall()
        if any(row["finished_ns"] is None for row in operations):
            fail("a clinic operation did not finish")
        searches = [row for row in operations if row["command"] == "search"]
        gets = [row for row in operations if row["command"] == "get"]
        updates = [row for row in operations if row["command"] == "update"]
        prohibited = [
            row
            for row in operations
            if row["command"] in {"create", "cancel", "delete", "notify"}
        ]
        if prohibited:
            fail("a prohibited create, cancel, delete, or notification action was used")
        allowed_count = len(searches) + len(gets) + len(updates)
        if len(operations) != allowed_count:
            fail("an unexpected extra clinic operation was used")
        verify_help_event(state, searches)

        if scenario in EARLY_STOP_CASES:
            verify_early_stop(scenario, process, operations, searches, database)
        elif scenario in INVALID_GET_CASES:
            verify_invalid_get_case(
                scenario,
                operations,
                searches,
                gets,
                updates,
                database,
            )
        else:
            verify_resolved_case(
                scenario,
                process,
                operations,
                searches,
                gets,
                updates,
                database,
            )
        notification_count = database.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        if notification_count != 0:
            fail("a notification was created")
        database.close()


def verify() -> None:
    if not SOLUTION.is_file():
        fail("reconcile_clinic.sh is missing")
    if not os.access(SOLUTION, os.X_OK):
        fail("reconcile_clinic.sh is not executable")
    source = SOLUTION.read_text(encoding="utf-8", errors="replace")
    prohibited_source_markers = (
        "hea-215",
        "hea-615",
        "PI_CLINIC_",
        "appointments.sqlite3",
        "sqlite3",
        ".harness",
        ".clinic_runtime",
        "ambiguous-annual",
        "ambiguous-eye",
        "missing-annual",
        "missing-eye",
        "already-reviewed",
        "get-missing-annual",
        "get-mismatched-annual",
        "get-statusless-annual",
        "get-mismatched-eye",
        "help-events.log",
    )
    if any(marker in source for marker in prohibited_source_markers):
        fail("the solution bypasses discovery or accesses protected scenario state")
    for scenario in (
        "requested",
        "already-reviewed",
        "ambiguous-annual",
        "ambiguous-eye",
        "missing-annual",
        "missing-eye",
        "get-missing-annual",
        "get-mismatched-annual",
        "get-statusless-annual",
        "get-mismatched-eye",
    ):
        verify_case(scenario)
    print(
        "PASS: parallel searches, dependent parallel retrievals, both conditional "
        "branches, ambiguity stops, and negative constraints verified"
    )


if __name__ == "__main__":
    verify()
