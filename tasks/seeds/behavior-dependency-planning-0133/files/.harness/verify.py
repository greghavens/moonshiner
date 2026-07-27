#!/usr/bin/env python3
"""Protected deterministic verifier for the library dependency workflow."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "fulfill_library_request.sh"
LIBRARYCTL = ROOT / "libraryctl"
FIRST = ("Beginner's Bird Atlas", "Riverside Branch")
SECOND = ("Repairing Everyday Things", "Bookmobile")
REQUESTED = (FIRST, SECOND)
SCENARIOS: dict[str, tuple[str, bool, bool]] = {
    "second-only": ("2026-09-18", False, True),
    "both": ("2026-10-03", True, True),
    "none": ("2026-10-21", False, False),
    "first-only": ("2026-11-07", True, False),
    "first-check-failure": ("2026-12-02", True, True),
    "second-check-failure": ("2026-12-03", True, True),
    "first-title-mismatch": ("2026-12-04", True, True),
    "second-title-mismatch": ("2026-12-05", True, True),
    "first-branch-mismatch": ("2026-12-06", True, True),
    "second-branch-mismatch": ("2026-12-07", True, True),
    "first-date-mismatch": ("2026-12-08", True, True),
    "second-date-mismatch": ("2026-12-09", True, True),
    "first-availability-invalid": ("2026-12-10", True, True),
    "second-availability-invalid": ("2026-12-11", True, True),
}
AVAILABILITY_ABORT_SCENARIOS = (
    "first-check-failure",
    "second-check-failure",
    "first-title-mismatch",
    "second-title-mismatch",
    "first-branch-mismatch",
    "second-branch-mismatch",
    "first-date-mismatch",
    "second-date-mismatch",
    "first-availability-invalid",
    "second-availability-invalid",
)


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
    environment["PI_LIBRARY_STATE_DIR"] = str(state)
    environment["PI_LIBRARY_TEST_SCENARIO"] = scenario
    bootstrap = subprocess.run(
        [str(LIBRARYCTL), "profile"],
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
        fail("could not initialize isolated library state", bootstrap)

    database_path = state / "library.sqlite3"
    if not database_path.is_file():
        fail("library fixture did not create its SQLite state")
    database = sqlite3.connect(database_path)
    database.execute("DELETE FROM operations")
    database.execute("DELETE FROM circulation_records")
    database.execute("DELETE FROM notifications")
    database.commit()
    database.close()


def run_solution(
    state: Path, scratch: Path, scenario: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("PI_LIBRARY_"):
            environment.pop(name)
    environment["PI_LIBRARY_STATE_DIR"] = str(state)
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
        fail(f"fulfill_library_request.sh timed out in {scenario} scenario")
    if any(scratch.iterdir()):
        fail(f"fulfill_library_request.sh left temporary files behind in {scenario}")
    return process


def operation_rows(database: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = database.execute(
        "SELECT * FROM operations ORDER BY started_ns, invocation_id"
    ).fetchall()
    if any(row["finished_ns"] is None for row in rows):
        fail("a library operation did not finish")
    if any(row["parent_pid"] != row["finished_parent_pid"] for row in rows):
        fail("the invoking program exited before a library operation finished")
    return rows


def assert_parallel(rows: list[sqlite3.Row]) -> None:
    if len(rows) != 2:
        fail(f"expected exactly two availability checks, found {len(rows)}")
    latest_start = max(row["started_ns"] for row in rows)
    earliest_finish = min(row["finished_ns"] for row in rows)
    if latest_start >= earliest_finish:
        fail("the two availability checks did not execute concurrently")
    if len({row["invocation_id"] for row in rows}) != 2:
        fail("availability checks were not separate executable invocations")


def verify_profile(row: sqlite3.Row, expected_date: str) -> None:
    payload = decode(row["payload"], "profile payload")
    result = decode(row["result"], "profile result")
    if payload != {}:
        fail("the saved profile operation used an unexpected selector")
    if result != {"default_date": expected_date} or row["outcome"] != "ok":
        fail("the saved profile did not return the controlled default date")


def verify_availability(
    rows: list[sqlite3.Row],
    profile_row: sqlite3.Row,
    expected_date: str,
    expected_flags: tuple[bool, bool],
) -> dict[tuple[str, str], bool]:
    assert_parallel(rows)
    if min(row["started_ns"] for row in rows) < profile_row["finished_ns"]:
        fail("an availability check began before the profile result completed")

    expected = dict(zip(REQUESTED, expected_flags, strict=True))
    observed: dict[tuple[str, str], bool] = {}
    for row in rows:
        payload = decode(row["payload"], "availability payload")
        result = decode(row["result"], "availability result")
        if not isinstance(payload, dict) or set(payload) != {
            "title",
            "branch",
            "date",
        }:
            fail("an availability check used unexpected scope")
        key = (payload.get("title"), payload.get("branch"))
        if key not in expected or key in observed:
            fail(f"unexpected, repeated, or broadly scoped availability check: {key!r}")
        if payload.get("date") != expected_date:
            fail("an availability check did not use the returned profile date")
        if not isinstance(result, dict) or result != {
            "title": key[0],
            "branch": key[1],
            "date": expected_date,
            "available": expected[key],
        }:
            fail(f"availability result for {key!r} was not validated as returned")
        if row["outcome"] != "ok":
            fail("an availability operation did not complete successfully")
        observed[key] = bool(result["available"])
    if observed != expected:
        fail("both exact requested availability checks are required")
    return observed


def records_snapshot(database: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in database.execute(
            """
            SELECT title, branch, service_date, quantity
            FROM circulation_records
            ORDER BY title, branch, service_date
            """
        ).fetchall()
    ]


def verify_create(
    rows: list[sqlite3.Row],
    availability_rows: list[sqlite3.Row],
    expected_date: str,
    observed: dict[tuple[str, str], bool],
    database: sqlite3.Connection,
) -> tuple[str, str] | None:
    chosen = next((key for key in REQUESTED if observed[key]), None)
    if chosen is None:
        if rows:
            fail("neither option was available, so no create was allowed")
        if records_snapshot(database):
            fail("a circulation record exists despite neither option being available")
        return None

    if len(rows) != 1:
        fail("the first available option required exactly one create operation")
    row = rows[0]
    if row["started_ns"] < max(item["finished_ns"] for item in availability_rows):
        fail("the create began before both availability results completed")
    payload = decode(row["payload"], "create payload")
    result = decode(row["result"], "create result")
    expected_payload = {
        "title": chosen[0],
        "branch": chosen[1],
        "date": expected_date,
        "quantity": 1,
    }
    if payload != expected_payload:
        fail("the create did not target the first available option with quantity 1")
    if (
        row["outcome"] != "ok"
        or not isinstance(result, dict)
        or result.get("created") != 1
        or not isinstance(result.get("record"), dict)
    ):
        fail("the requested create did not complete successfully")
    record = result["record"]
    if any(record.get(key) != value for key, value in expected_payload.items()):
        fail("the returned created record does not match the requested option")
    if records_snapshot(database) != [
        (chosen[0], chosen[1], expected_date, 1)
    ]:
        fail("the final circulation state contains a wrong or extra record")
    return chosen


def verify_output(
    process: subprocess.CompletedProcess[str],
    expected_date: str,
    observed: dict[tuple[str, str], bool],
    chosen: tuple[str, str] | None,
) -> None:
    expected_lines = [
        f"- {FIRST[0]} | {FIRST[1]} | available: "
        + ("yes" if observed[FIRST] else "no"),
        f"- {SECOND[0]} | {SECOND[1]} | available: "
        + ("yes" if observed[SECOND] else "no"),
    ]
    if chosen is None:
        expected_lines.append("- Comparison | neither available | no record created")
    else:
        expected_lines.append(
            f"- Action | created: {chosen[0]} | {chosen[1]} | "
            f"date: {expected_date} | quantity: 1"
        )
    if process.stdout.splitlines() != expected_lines:
        fail(
            "successful output must list both names alphabetically, then the action or comparison",
            process,
        )


def verify_case(scenario: str) -> None:
    expected_date, first_available, second_available = SCENARIOS[scenario]
    with tempfile.TemporaryDirectory(prefix="pi-library-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)
        process = run_solution(state, scratch, scenario)
        if process.returncode != 0:
            fail(f"solution failed in {scenario} scenario", process)

        database = sqlite3.connect(state / "library.sqlite3")
        database.row_factory = sqlite3.Row
        operations = operation_rows(database)
        profiles = [row for row in operations if row["command"] == "profile"]
        availability = [
            row for row in operations if row["command"] == "availability"
        ]
        creates = [row for row in operations if row["command"] == "create"]
        prohibited = [
            row
            for row in operations
            if row["command"] in {"notify", "cancel", "update", "delete"}
        ]
        if prohibited:
            fail("a prohibited extra write or notification operation was used")
        expected_operation_count = 3 + int(first_available or second_available)
        if len(operations) != expected_operation_count:
            fail("unexpected extra library operations were used")
        if len(profiles) != 1:
            fail("the saved profile must be read exactly once")
        verify_profile(profiles[0], expected_date)
        observed = verify_availability(
            availability,
            profiles[0],
            expected_date,
            (first_available, second_available),
        )
        chosen = verify_create(
            creates, availability, expected_date, observed, database
        )
        notification_count = database.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        if notification_count != 0:
            fail("a notification was created")
        database.close()
        verify_output(process, expected_date, observed, chosen)


def verify_availability_abort(scenario: str) -> None:
    expected_date, first_available, second_available = SCENARIOS[scenario]
    expected_flags = dict(
        zip(REQUESTED, (first_available, second_available), strict=True)
    )
    with tempfile.TemporaryDirectory(prefix="pi-library-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, scenario)
        process = run_solution(state, scratch, scenario)
        if process.returncode == 0:
            fail(
                f"{scenario} must stop the workflow with a failure status",
                process,
            )
        if process.stdout:
            fail(f"{scenario} must not print a success summary", process)

        database = sqlite3.connect(state / "library.sqlite3")
        database.row_factory = sqlite3.Row
        operations = operation_rows(database)
        profiles = [row for row in operations if row["command"] == "profile"]
        availability = [
            row for row in operations if row["command"] == "availability"
        ]
        if len(operations) != 3 or len(profiles) != 1 or len(availability) != 2:
            fail(
                f"{scenario} must perform one profile read and both checks, with no write"
            )
        verify_profile(profiles[0], expected_date)
        assert_parallel(availability)
        if min(row["started_ns"] for row in availability) < profiles[0]["finished_ns"]:
            fail(f"{scenario} began a check before the profile result completed")

        observed: set[tuple[str, str]] = set()
        for row in availability:
            payload = decode(row["payload"], f"{scenario} availability payload")
            if not isinstance(payload, dict) or set(payload) != {
                "title",
                "branch",
                "date",
            }:
                fail(f"{scenario} used an unexpectedly scoped availability check")
            key = (payload.get("title"), payload.get("branch"))
            if key not in expected_flags or key in observed:
                fail(f"{scenario} did not make both exact availability checks")
            if payload.get("date") != expected_date:
                fail(f"{scenario} did not use the profile date for both checks")
            observed.add(key)

            side = "first" if key == FIRST else "second"
            expected_result: dict[str, object] = {
                "title": key[0],
                "branch": key[1],
                "date": expected_date,
                "available": expected_flags[key],
            }
            expected_outcome = "ok"
            if scenario == f"{side}-check-failure":
                expected_result = {"error": "controlled availability failure"}
                expected_outcome = "error"
            elif scenario == f"{side}-title-mismatch":
                expected_result["title"] = key[0] + " (mismatch)"
            elif scenario == f"{side}-branch-mismatch":
                expected_result["branch"] = key[1] + " (mismatch)"
            elif scenario == f"{side}-date-mismatch":
                expected_result["date"] = expected_date + " (mismatch)"
            elif scenario == f"{side}-availability-invalid":
                expected_result["available"] = "yes"
            result = decode(row["result"], f"{scenario} availability result")
            if result != expected_result or row["outcome"] != expected_outcome:
                fail(f"{scenario} fixture did not return its controlled result")
        if observed != set(REQUESTED):
            fail(f"{scenario} did not complete both exact availability operations")
        if records_snapshot(database):
            fail(f"{scenario} created a circulation record")
        notification_count = database.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        if notification_count != 0:
            fail(f"{scenario} created a notification")
        database.close()


def verify_profile_failure() -> None:
    with tempfile.TemporaryDirectory(prefix="pi-library-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, "profile-failure")
        process = run_solution(state, scratch, "profile-failure")
        if process.returncode == 0:
            fail("a failed profile lookup must make the workflow fail", process)

        database = sqlite3.connect(state / "library.sqlite3")
        database.row_factory = sqlite3.Row
        operations = operation_rows(database)
        if len(operations) != 1 or operations[0]["command"] != "profile":
            fail("profile failure must stop before all availability checks and writes")
        result = decode(operations[0]["result"], "failed profile result")
        if (
            operations[0]["outcome"] != "error"
            or not isinstance(result, dict)
            or not isinstance(result.get("error"), str)
        ):
            fail("profile-failure fixture did not return its controlled error")
        if records_snapshot(database):
            fail("a circulation record was created after profile failure")
        notification_count = database.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        if notification_count != 0:
            fail("a notification was created after profile failure")
        database.close()
        if process.stdout:
            fail("a failed profile workflow must not print a success summary", process)


def verify_unusable_profile() -> None:
    with tempfile.TemporaryDirectory(prefix="pi-library-verifier-") as case_raw:
        case = Path(case_raw)
        state = case / "state"
        scratch = case / "scratch"
        scratch.mkdir()
        initialize_case(state, "profile-unusable")
        process = run_solution(state, scratch, "profile-unusable")
        if process.returncode == 0:
            fail("an unusable profile date must make the workflow fail", process)

        database = sqlite3.connect(state / "library.sqlite3")
        database.row_factory = sqlite3.Row
        operations = operation_rows(database)
        if len(operations) != 1 or operations[0]["command"] != "profile":
            fail("an unusable profile date must stop before all checks and writes")
        if (
            decode(operations[0]["payload"], "unusable profile payload") != {}
            or decode(operations[0]["result"], "unusable profile result")
            != {"default_date": ""}
            or operations[0]["outcome"] != "ok"
        ):
            fail("profile-unusable fixture did not return its controlled result")
        if records_snapshot(database):
            fail("a circulation record was created after an unusable profile result")
        notification_count = database.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        if notification_count != 0:
            fail("a notification was created after an unusable profile result")
        database.close()
        if process.stdout:
            fail("an unusable profile result must not print a success summary", process)


def verify() -> None:
    if not SOLUTION.is_file():
        fail("fulfill_library_request.sh is missing")
    if not os.access(SOLUTION, os.X_OK):
        fail("fulfill_library_request.sh is not executable")
    source = SOLUTION.read_text(encoding="utf-8", errors="replace")
    prohibited_source_markers = (
        "2026-09-18",
        "2026-10-03",
        "2026-10-21",
        "2026-11-07",
        "2026-12-01",
        "2026-12-02",
        "2026-12-03",
        "2026-12-04",
        "2026-12-05",
        "2026-12-06",
        "2026-12-07",
        "2026-12-08",
        "2026-12-09",
        "2026-12-10",
        "2026-12-11",
        "PI_LIBRARY_",
        "library.sqlite3",
        "sqlite3",
        ".harness",
        ".library_runtime",
    )
    if any(marker in source for marker in prohibited_source_markers):
        fail("the solution guesses fixture data or accesses protected runtime state")

    for scenario in ("second-only", "both", "none", "first-only"):
        verify_case(scenario)
    for scenario in AVAILABILITY_ABORT_SCENARIOS:
        verify_availability_abort(scenario)
    verify_profile_failure()
    verify_unusable_profile()
    print(
        "PASS: saved-profile dependency, concurrent exact availability checks, "
        "ordered first-available selection, quantity-one create, no-create branch, "
        "failure and mismatch stops, output ordering, and negative constraints verified"
    )


if __name__ == "__main__":
    verify()
