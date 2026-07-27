#!/usr/bin/env python3
"""Protected verifier for the saved-profile expense dependency workflow."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "fulfill_expense_request.sh"
EXPENSESCTL = ROOT / "expensesctl"
FIRST = ("opt-134-coastal", "Coastal survey lodging", "Denver")
SECOND = ("opt-134-regional", "Regional conference registration", "Seattle")
REQUESTED = (FIRST, SECOND)

# date, first available, second available, create response omits status
SCENARIOS: dict[str, tuple[str, bool, bool, bool]] = {
    "second-only": ("2026-11-25", False, True, False),
    "both": ("2027-01-14", True, True, False),
    "none": ("2027-02-09", False, False, False),
    "first-only": ("2027-03-18", True, False, False),
    "sparse-create": ("2027-04-22", False, True, True),
    "profile-failure": ("2027-05-11", False, True, False),
    "create-failure": ("2027-05-20", False, True, False),
    "create-unconfirmed": ("2027-05-27", False, True, False),
    "availability-failure": ("2027-06-03", True, True, False),
    "availability-mismatch": ("2027-06-17", True, True, False),
    "availability-ambiguous": ("2027-07-01", True, True, False),
    "availability-missing-boolean": ("2027-07-15", True, True, False),
    "availability-empty-id": ("2027-07-29", True, True, False),
    "profile-missing-date": ("2027-08-12", False, True, False),
    "profile-ambiguous-date": ("2027-08-26", False, True, False),
}

NORMAL_SCENARIOS = ("second-only", "both", "none", "first-only", "sparse-create")
INVALID_AVAILABILITY_SCENARIOS = (
    "availability-failure",
    "availability-mismatch",
    "availability-ambiguous",
    "availability-missing-boolean",
    "availability-empty-id",
)
PROFILE_STOP_SCENARIOS = (
    "profile-failure",
    "profile-missing-date",
    "profile-ambiguous-date",
)
CREATE_STOP_SCENARIOS = ("create-failure", "create-unconfirmed")


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


def initialize_case(state: Path, scenario: str) -> None:
    environment = os.environ.copy()
    environment["PI_EXPENSES_STATE_DIR"] = str(state)
    environment["PI_EXPENSES_TEST_SCENARIO"] = scenario
    bootstrap = subprocess.run(
        [str(EXPENSESCTL), "profile"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if scenario == "profile-failure":
        if bootstrap.returncode == 0:
            fail("profile-failure fixture unexpectedly succeeded")
    elif bootstrap.returncode != 0:
        fail("could not initialize isolated expense state", bootstrap)

    database_path = state / "expenses.sqlite3"
    if not database_path.is_file():
        fail("expense fixture did not create its SQLite database")
    database = sqlite3.connect(database_path)
    database.execute("DELETE FROM operations")
    database.execute("DELETE FROM expense_records WHERE id LIKE 'exp-c%'")
    database.execute("DELETE FROM notifications")
    database.commit()
    database.close()


def run_solution(
    state: Path, scratch: Path, scenario: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("PI_EXPENSES_"):
            environment.pop(name)
    environment["PI_EXPENSES_STATE_DIR"] = str(state)
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
        fail(f"fulfill_expense_request.sh timed out in {scenario} scenario")
    if any(scratch.iterdir()):
        fail(f"fulfill_expense_request.sh left temporary files behind in {scenario}")
    return process


def operation_rows(database: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = database.execute(
        "SELECT * FROM operations ORDER BY started_ns, invocation_id"
    ).fetchall()
    if any(row["finished_ns"] is None for row in rows):
        fail("an expense operation did not finish")
    return rows


def records_snapshot(database: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in database.execute(
            """
            SELECT id, option_id, name, location, service_date, quantity, status
            FROM expense_records
            ORDER BY id
            """
        ).fetchall()
    ]


def assert_parallel(rows: list[sqlite3.Row]) -> None:
    if len(rows) != 2:
        fail(f"expected exactly two availability checks, found {len(rows)}")
    latest_start = max(row["started_ns"] for row in rows)
    earliest_finish = min(row["finished_ns"] for row in rows)
    if latest_start >= earliest_finish:
        fail("the availability checks did not execute concurrently")
    if len({row["invocation_id"] for row in rows}) != 2:
        fail("availability checks were not separate executable invocations")


def verify_profile(row: sqlite3.Row, expected_date: str) -> None:
    payload = decode(row["payload"], "profile payload")
    result = decode(row["result"], "profile result")
    if payload != {}:
        fail("the profile operation used an unexpected selector")
    if result != {"default_date": expected_date, "preferred_quantity": 1}:
        fail("the saved profile did not return the controlled default date")
    if row["outcome"] != "ok":
        fail("the saved profile operation did not succeed")


def availability_result(
    key: tuple[str, str, str], expected_date: str, available: bool
) -> dict[str, object]:
    return {
        "option_id": key[0],
        "name": key[1],
        "location": key[2],
        "date": expected_date,
        "available": available,
    }


def verify_availability(
    rows: list[sqlite3.Row],
    profile_row: sqlite3.Row,
    expected_date: str,
    expected_flags: tuple[bool, bool],
) -> dict[tuple[str, str, str], tuple[bool, dict[str, object]]]:
    assert_parallel(rows)
    if min(row["started_ns"] for row in rows) < profile_row["finished_ns"]:
        fail("an availability check began before the profile result completed")

    expected = dict(zip(REQUESTED, expected_flags, strict=True))
    observed: dict[tuple[str, str, str], tuple[bool, dict[str, object]]] = {}
    for row in rows:
        payload = decode(row["payload"], "availability payload")
        result = decode(row["result"], "availability result")
        if not isinstance(payload, dict) or set(payload) != {"name", "location", "date"}:
            fail("an availability check used unexpected scope")
        matching = [
            key
            for key in REQUESTED
            if (key[1], key[2]) == (payload.get("name"), payload.get("location"))
        ]
        if len(matching) != 1:
            fail("an availability check did not target one exact requested option")
        key = matching[0]
        if key in observed:
            fail("an exact availability check was repeated")
        if payload.get("date") != expected_date:
            fail("an availability check did not use the returned profile date")
        expected_result = availability_result(key, expected_date, expected[key])
        if not isinstance(result, dict) or result != expected_result:
            fail("an availability result was not used as returned")
        if row["outcome"] != "ok":
            fail("an availability operation did not complete successfully")
        observed[key] = (expected[key], result)
    if set(observed) != set(REQUESTED):
        fail("both exact requested availability checks are required")
    return observed


def verify_create(
    rows: list[sqlite3.Row],
    availability_rows: list[sqlite3.Row],
    expected_date: str,
    observed: dict[tuple[str, str, str], tuple[bool, dict[str, object]]],
    baseline: list[tuple[object, ...]],
    database: sqlite3.Connection,
    omit_status: bool,
) -> tuple[tuple[str, str, str] | None, dict[str, object] | None]:
    chosen = next((key for key in REQUESTED if observed[key][0]), None)
    if chosen is None:
        if rows:
            fail("neither option was available, so no create was allowed")
        if records_snapshot(database) != baseline:
            fail("expense records changed despite neither option being available")
        return None, None

    if len(rows) != 1:
        fail("the first available option required exactly one create operation")
    row = rows[0]
    if row["started_ns"] < max(item["finished_ns"] for item in availability_rows):
        fail("the create began before both availability results completed")
    payload = decode(row["payload"], "create payload")
    expected_payload = {
        "option_id": chosen[0],
        "date": expected_date,
        "quantity": 1,
    }
    if payload != expected_payload:
        fail("the create did not use the first available returned option with quantity 1")
    result = decode(row["result"], "create result")
    if (
        row["outcome"] != "ok"
        or not isinstance(result, dict)
        or result.get("created") != 1
        or not isinstance(result.get("record"), dict)
    ):
        fail("the requested create did not complete successfully")
    record = result["record"]
    expected_record = {
        "id": "exp-c001",
        "name": chosen[1],
        "location": chosen[2],
        "date": expected_date,
        "quantity": 1,
        "status": "submitted",
    }
    if omit_status:
        expected_record.pop("status")
    if record != expected_record:
        fail("the created record response did not match the selected option")
    expected_state = sorted(
        baseline
        + [
            (
                "exp-c001",
                chosen[0],
                chosen[1],
                chosen[2],
                expected_date,
                1,
                "submitted",
            )
        ],
        key=lambda item: str(item[0]),
    )
    if records_snapshot(database) != expected_state:
        fail("the final expense state contains a wrong, altered, or extra record")
    return chosen, record


def rendered(value: object) -> str:
    if value is None:
        return "unknown"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def availability_line(value: object) -> str:
    returned = value if isinstance(value, dict) else {}
    return (
        "- name: {name}; location: {location}; date: {date}; "
        "available: {available}; option_id: {option_id}"
    ).format(
        **{
            field: rendered(returned.get(field))
            for field in ("name", "location", "date", "available", "option_id")
        }
    )


def verify_output(
    process: subprocess.CompletedProcess[str],
    observed: dict[tuple[str, str, str], tuple[bool, dict[str, object]]],
    record: dict[str, object] | None,
) -> None:
    expected_lines: list[str] = []
    for key in REQUESTED:
        expected_lines.append(availability_line(observed[key][1]))
    if record is not None:
        expected_lines.append(
            "- id: {id}; name: {name}; location: {location}; date: {date}; "
            "quantity: {quantity}; status: {status}".format(
                **{
                    field: rendered(record.get(field))
                    for field in ("id", "name", "location", "date", "quantity", "status")
                }
            )
        )
    if process.stdout.splitlines() != expected_lines:
        fail("stdout was not grounded only in returned fields with missing values unknown", process)


def verify_case(scenario: str) -> None:
    expected_date, first_available, second_available, omit_status = SCENARIOS[scenario]
    with tempfile.TemporaryDirectory(prefix="pi-expenses-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)
        database = sqlite3.connect(state / "expenses.sqlite3")
        baseline = records_snapshot(database)
        database.close()

        process = run_solution(state, scratch, scenario)
        if process.returncode != 0:
            fail(f"solution failed in {scenario} scenario", process)

        database = sqlite3.connect(state / "expenses.sqlite3")
        database.row_factory = sqlite3.Row
        operations = operation_rows(database)
        profiles = [row for row in operations if row["command"] == "profile"]
        availability = [row for row in operations if row["command"] == "availability"]
        creates = [row for row in operations if row["command"] == "create"]
        prohibited = [
            row
            for row in operations
            if row["command"] in {"update", "cancel", "delete", "notify"}
        ]
        if prohibited:
            fail("a prohibited extra write or notification operation was used")
        expected_operation_count = 3 + int(first_available or second_available)
        if len(operations) != expected_operation_count:
            fail("unexpected extra expense operations were used")
        if len(profiles) != 1:
            fail("the saved operational profile must be read exactly once")
        verify_profile(profiles[0], expected_date)
        observed = verify_availability(
            availability,
            profiles[0],
            expected_date,
            (first_available, second_available),
        )
        _, record = verify_create(
            creates,
            availability,
            expected_date,
            observed,
            baseline,
            database,
            omit_status,
        )
        if database.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] != 0:
            fail("a notification was created")
        database.close()
        verify_output(process, observed, record)


def invalid_availability_results(
    scenario: str, expected_date: str
) -> tuple[object, object, object, object, str, str]:
    first: object = availability_result(FIRST, expected_date, True)
    second: object = availability_result(SECOND, expected_date, True)
    first_stdout: object = first
    second_stdout: object = second
    first_outcome = "ok"
    second_outcome = "ok"
    if scenario == "availability-failure":
        first = {"error": "availability service unavailable"}
        first_stdout = {}
        first_outcome = "error"
    elif scenario == "availability-mismatch":
        first = {**first, "name": "Coastal survey lodging mismatch"}
        first_stdout = first
    elif scenario == "availability-ambiguous":
        first = [first, {**first, "option_id": "opt-ambiguous-134"}]
        first_stdout = first
    elif scenario == "availability-missing-boolean":
        first = {key: value for key, value in first.items() if key != "available"}
        first_stdout = first
    elif scenario == "availability-empty-id":
        second = {**second, "option_id": ""}
        second_stdout = second
    else:
        fail(f"unknown invalid-availability scenario: {scenario}")
    return (
        first,
        second,
        first_stdout,
        second_stdout,
        first_outcome,
        second_outcome,
    )


def verify_invalid_availability(scenario: str) -> None:
    expected_date = SCENARIOS[scenario][0]
    with tempfile.TemporaryDirectory(prefix="pi-expenses-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)
        database = sqlite3.connect(state / "expenses.sqlite3")
        baseline = records_snapshot(database)
        database.close()

        process = run_solution(state, scratch, scenario)
        if process.returncode == 0:
            fail(f"invalid availability data must fail in {scenario}", process)

        database = sqlite3.connect(state / "expenses.sqlite3")
        database.row_factory = sqlite3.Row
        operations = operation_rows(database)
        profiles = [row for row in operations if row["command"] == "profile"]
        availability = [row for row in operations if row["command"] == "availability"]
        creates = [row for row in operations if row["command"] == "create"]
        if len(operations) != 3 or len(profiles) != 1 or len(availability) != 2:
            fail("invalid availability data required one profile and both exact checks")
        if creates:
            fail("invalid availability data must stop before create")
        verify_profile(profiles[0], expected_date)
        assert_parallel(availability)
        if min(row["started_ns"] for row in availability) < profiles[0]["finished_ns"]:
            fail("an availability check began before the profile result completed")

        (
            expected_first,
            expected_second,
            first_stdout,
            second_stdout,
            first_outcome,
            second_outcome,
        ) = invalid_availability_results(scenario, expected_date)
        expected_by_key = {FIRST: expected_first, SECOND: expected_second}
        seen: set[tuple[str, str, str]] = set()
        for row in availability:
            payload = decode(row["payload"], "invalid availability payload")
            if not isinstance(payload, dict) or set(payload) != {"name", "location", "date"}:
                fail("an invalid-case availability check used unexpected scope")
            matching = [
                key
                for key in REQUESTED
                if (key[1], key[2], expected_date)
                == (payload.get("name"), payload.get("location"), payload.get("date"))
            ]
            if len(matching) != 1 or matching[0] in seen:
                fail("invalid-case checks were not the two distinct requested options")
            key = matching[0]
            seen.add(key)
            if decode(row["result"], "invalid availability result") != expected_by_key[key]:
                fail("the controlled invalid availability result changed")
            expected_outcome = first_outcome if key == FIRST else second_outcome
            if row["outcome"] != expected_outcome:
                fail("the controlled invalid availability outcome changed")
        if seen != set(REQUESTED):
            fail("both requested checks must complete before invalid data is rejected")
        if records_snapshot(database) != baseline:
            fail("expense state changed after invalid availability data")
        if database.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] != 0:
            fail("a notification was created after invalid availability data")
        database.close()

        expected_stdout = [
            availability_line(first_stdout),
            availability_line(second_stdout),
        ]
        if process.stdout.splitlines() != expected_stdout:
            fail("invalid availability output was not sparse and grounded", process)


def verify_create_stop(scenario: str) -> None:
    expected_date, first_available, second_available, _ = SCENARIOS[scenario]
    with tempfile.TemporaryDirectory(prefix="pi-expenses-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)
        database = sqlite3.connect(state / "expenses.sqlite3")
        baseline = records_snapshot(database)
        database.close()

        process = run_solution(state, scratch, scenario)
        if process.returncode == 0:
            fail("an unconfirmed create must make the workflow fail", process)

        database = sqlite3.connect(state / "expenses.sqlite3")
        database.row_factory = sqlite3.Row
        operations = operation_rows(database)
        profiles = [row for row in operations if row["command"] == "profile"]
        availability = [row for row in operations if row["command"] == "availability"]
        creates = [row for row in operations if row["command"] == "create"]
        if len(operations) != 4 or len(profiles) != 1 or len(creates) != 1:
            fail("create failure did not use exactly the required four operations")
        verify_profile(profiles[0], expected_date)
        observed = verify_availability(
            availability,
            profiles[0],
            expected_date,
            (first_available, second_available),
        )
        create = creates[0]
        if create["started_ns"] < max(row["finished_ns"] for row in availability):
            fail("the failed create began before both checks completed")
        expected_payload = {
            "option_id": SECOND[0],
            "date": expected_date,
            "quantity": 1,
        }
        if decode(create["payload"], "failed create payload") != expected_payload:
            fail("the failed create did not target the first available returned option")
        expected_outcome = "error" if scenario == "create-failure" else "ok"
        if (
            create["outcome"] != expected_outcome
            or decode(create["result"], "unconfirmed create result")
            != {"created": 0, "record": None}
        ):
            fail("the controlled unconfirmed create result changed")
        if records_snapshot(database) != baseline:
            fail("expense state changed after an unconfirmed create")
        if database.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] != 0:
            fail("a notification was created after an unconfirmed create")
        database.close()
        verify_output(process, observed, None)


def verify_profile_stop(scenario: str) -> None:
    with tempfile.TemporaryDirectory(prefix="pi-expenses-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)
        database = sqlite3.connect(state / "expenses.sqlite3")
        baseline = records_snapshot(database)
        database.close()

        process = run_solution(state, scratch, scenario)
        if process.returncode == 0:
            fail("an unusable profile result must make the workflow fail", process)

        database = sqlite3.connect(state / "expenses.sqlite3")
        database.row_factory = sqlite3.Row
        operations = operation_rows(database)
        if len(operations) != 1 or operations[0]["command"] != "profile":
            fail("an unusable profile must stop before all checks and writes")
        if decode(operations[0]["payload"], "profile-stop payload") != {}:
            fail("the profile-stop operation used an unexpected selector")
        date = SCENARIOS[scenario][0]
        expected_results: dict[str, tuple[object, str]] = {
            "profile-failure": (
                {"error": "saved operational profile is unavailable"},
                "error",
            ),
            "profile-missing-date": ({"preferred_quantity": 1}, "ok"),
            "profile-ambiguous-date": (
                {
                    "default_date": [date, "2027-08-27"],
                    "preferred_quantity": 1,
                },
                "ok",
            ),
        }
        expected_result, expected_outcome = expected_results[scenario]
        if (
            decode(operations[0]["result"], "profile-stop result") != expected_result
            or operations[0]["outcome"] != expected_outcome
        ):
            fail("the controlled unusable profile result changed")
        if records_snapshot(database) != baseline:
            fail("expense state changed after an unusable profile result")
        if database.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] != 0:
            fail("a notification was created after an unusable profile result")
        database.close()
        if process.stdout:
            fail("an unusable profile workflow must not print a report", process)


def verify() -> None:
    if not SOLUTION.is_file():
        fail("fulfill_expense_request.sh is missing")
    if not os.access(SOLUTION, os.X_OK):
        fail("fulfill_expense_request.sh is not executable")
    source = SOLUTION.read_text(encoding="utf-8", errors="replace")
    prohibited_source_markers = (
        "2026-11-25",
        "2027-01-14",
        "2027-02-09",
        "2027-03-18",
        "2027-04-22",
        "2027-05-11",
        "2027-05-20",
        "2027-05-27",
        "2027-06-03",
        "2027-06-17",
        "2027-07-01",
        "2027-07-15",
        "2027-07-29",
        "2027-08-12",
        "2027-08-26",
        "2027-08-27",
        "PI_EXPENSES_",
        "expenses.sqlite3",
        "sqlite3",
        ".harness",
        ".expenses_runtime",
    )
    if any(marker in source for marker in prohibited_source_markers):
        fail("the solution guesses fixture data or accesses protected runtime state")

    for scenario in NORMAL_SCENARIOS:
        verify_case(scenario)
    for scenario in INVALID_AVAILABILITY_SCENARIOS:
        verify_invalid_availability(scenario)
    for scenario in CREATE_STOP_SCENARIOS:
        verify_create_stop(scenario)
    for scenario in PROFILE_STOP_SCENARIOS:
        verify_profile_stop(scenario)
    print(
        "PASS: profile dependency, concurrent exact checks, ordered selection, "
        "quantity-one create, no-create branch, failure stop, grounded sparse output, "
        "and negative constraints verified"
    )


if __name__ == "__main__":
    verify()
