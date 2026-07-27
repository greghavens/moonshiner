#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-dependency-planning-0137."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / ".facilities" / "seed.sql"
RUNTIME_DIR = ROOT / ".facilities" / "runtime"
DATABASE_PATH = RUNTIME_DIR / "facilities.sqlite3"
AUDIT_PATH = RUNTIME_DIR / "audit.jsonl"
KEY_PATH = ROOT / ".protected" / "audit.key"
REFERENCE_MARKER_PATH = ROOT / ".reference_solution"
REFERENCE_STAMP_PATH = RUNTIME_DIR / ".reference-setup"
REFERENCE_MARKER_TEXT = "exercise genuine facilitiesctl commands for reference validation\n"
OPTIONS = [
    ("Community hall chair setup", "Civic Annex"),
    ("East wing signage request", "Operations Center"),
]
EXPECTED_HASHES = {
    "README.md": "41cb9a486b66841e15b6032a0dfe34611fb6c6f970faa7e93a74f2e1de95d68c",
    "facilitiesctl": "c84c79a44da3ccc735a71314a370165e8d8c46971c800ec92537b37911ad0e7a",
    ".facilities/seed.sql": "044d3707c285f0cbc0c96e595178acf87832ff6a3fe2e02427d0bdf2ce4fb182",
    ".protected/audit.key": "addc02104c05b520dbfcbb9cbeab40e287f2b6b645a58ef3f97b666b03751d7c",
    ".gitignore": "1ae1f20545184a3bb9187649ba498a39d42b4d84ca8bbb3792da081505b16f7b",
}
ALLOWED_ROOT_ENTRIES = {
    ".facilities",
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution",
    "README.md",
    "facilitiesctl",
}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected sandbox input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))


def verify_reference_setup_state() -> None:
    if not REFERENCE_STAMP_PATH.exists():
        return
    try:
        marker_is_current = (
            REFERENCE_MARKER_PATH.is_file()
            and REFERENCE_MARKER_PATH.read_text(encoding="utf-8")
            == REFERENCE_MARKER_TEXT
        )
    except OSError as error:
        fail(f"reference marker is unreadable: {error}")
    if not marker_is_current:
        fail("stale reference setup state remains after the reference fix was removed")


def connect_seed() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, sqlite3.Error) as error:
        connection.close()
        fail(f"protected seed is unusable: {error}")
    return connection


def connect_current() -> sqlite3.Connection:
    if not DATABASE_PATH.is_file():
        fail("the facilities registry was never executed")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as error:
        fail(f"runtime facilities database is unreadable: {error}")
    if integrity is None or integrity[0] != "ok":
        connection.close()
        fail("runtime facilities database failed its integrity check")
    return connection


def rows(connection: sqlite3.Connection, query: str) -> list[list[Any]]:
    return [list(row) for row in connection.execute(query).fetchall()]


def request_objects(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    records = []
    for row in connection.execute(
        """
        SELECT id, name, location, service_date AS date, quantity,
               status, archived, relation
        FROM requests ORDER BY id
        """
    ):
        record = dict(row)
        record["archived"] = bool(record["archived"])
        records.append(record)
    return records


def snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        "metadata": rows(connection, "SELECT key, value FROM metadata ORDER BY key"),
        "profile": rows(
            connection,
            "SELECT singleton, default_date, preferred_quantity FROM profile ORDER BY singleton",
        ),
        "availability": rows(
            connection,
            """
            SELECT option_id, name, location, service_date, available
            FROM availability ORDER BY option_id
            """,
        ),
        "requests": request_objects(connection),
        "notifications": rows(
            connection,
            """
            SELECT notification_id, request_id, message
            FROM notifications ORDER BY notification_id
            """,
        ),
    }


def expected_created_record(initial: sqlite3.Connection) -> tuple[dict[str, Any], str]:
    profile = initial.execute(
        "SELECT default_date FROM profile WHERE singleton = 1"
    ).fetchone()
    if profile is None:
        fail("protected profile fixture is missing")
    date = str(profile["default_date"])
    selected: tuple[str, str] | None = None
    for name, location in OPTIONS:
        row = initial.execute(
            """
            SELECT available FROM availability
            WHERE name = ? AND location = ? AND service_date = ?
            """,
            (name, location, date),
        ).fetchone()
        if row is not None and bool(row["available"]):
            selected = (name, location)
            break
    if selected is None:
        fail("protected fixture does not exercise the conditional create branch")
    request_id = initial.execute(
        "SELECT value FROM metadata WHERE key = 'created_record_id'"
    ).fetchone()
    if request_id is None:
        fail("protected created-record ID is missing")
    name, location = selected
    return (
        {
            "id": str(request_id["value"]),
            "name": name,
            "location": location,
            "date": date,
            "quantity": 1,
            "status": "scheduled",
            "archived": False,
            "relation": "requested",
        },
        date,
    )


def verify_final_state() -> tuple[dict[str, Any], str, dict[str, Any]]:
    initial_connection = connect_seed()
    current_connection = connect_current()
    try:
        initial = snapshot(initial_connection)
        current = snapshot(current_connection)
        expected_record, date = expected_created_record(initial_connection)
    finally:
        initial_connection.close()
        current_connection.close()

    for table in ("metadata", "profile", "availability", "notifications"):
        if current[table] != initial[table]:
            fail(f"out-of-scope {table} state changed")

    initial_requests = {record["id"]: record for record in initial["requests"]}
    current_requests = {record["id"]: record for record in current["requests"]}
    if len(initial_requests) != len(initial["requests"]) or len(current_requests) != len(
        current["requests"]
    ):
        fail("request IDs are not unique")
    if set(current_requests) != set(initial_requests) | {expected_record["id"]}:
        fail("the final state does not contain exactly one new request")
    for request_id, original in initial_requests.items():
        if current_requests.get(request_id) != original:
            fail(f"similarly named, related, archived, or other request changed: {request_id}")
    if current_requests.get(expected_record["id"]) != expected_record:
        fail("the new request is not exactly the first available requested option")
    return expected_record, date, initial


def load_events() -> list[dict[str, Any]]:
    if not AUDIT_PATH.is_file():
        fail("no genuine facilitiesctl execution evidence exists")
    try:
        key = KEY_PATH.read_bytes().strip()
        raw_lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"facilities audit evidence is unreadable: {error}")
    events: list[dict[str, Any]] = []
    for index, line in enumerate(raw_lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            fail(f"audit event {index} is invalid JSON: {error}")
        if not isinstance(event, dict):
            fail(f"audit event {index} is not an object")
        seal = event.get("seal")
        unsigned = dict(event)
        unsigned.pop("seal", None)
        expected_seal = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(seal, str) or not hmac.compare_digest(seal, expected_seal):
            fail(f"audit event {index} was not emitted intact by facilitiesctl")
        if event.get("sequence") != index:
            fail("audit sequence is incomplete or reordered")
        for field in (
            "started_ns",
            "finished_ns",
            "process_pid",
            "process_group_id",
            "session_id",
        ):
            if not isinstance(event.get(field), int) or isinstance(event.get(field), bool):
                fail(f"audit event {index} has invalid execution evidence")
        if event["started_ns"] >= event["finished_ns"]:
            fail(f"audit event {index} has an invalid execution interval")
        events.append(event)
    return events


def overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def same_harness_parent(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def separate_pi_calls(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first["process_group_id"] != second["process_group_id"]
        and first["process_pid"] == first["process_group_id"]
        and second["process_pid"] == second["process_group_id"]
        and first["session_id"] == first["process_pid"]
        and second["session_id"] == second["process_pid"]
    )


def verify_execution(
    events: list[dict[str, Any]],
    expected_record: dict[str, Any],
    date: str,
    initial: dict[str, Any],
) -> None:
    if len(events) != 4:
        fail(f"expected exactly four facilities-registry operations, found {len(events)}")
    if [event.get("operation") for event in events] != [
        "profile",
        "availability",
        "availability",
        "create",
    ]:
        fail("operations were missing, out of dependency order, or included an extra action")
    if any(event.get("success") is not True for event in events):
        fail("a required facilities-registry operation did not succeed")

    profile = events[0]
    expected_profile = {
        "default_date": initial["profile"][0][1],
        "preferred_quantity": initial["profile"][0][2],
    }
    if (
        profile.get("default_date") != date
        or profile.get("preferred_quantity") != expected_profile["preferred_quantity"]
        or profile.get("profile_sha256") != digest(expected_profile)
    ):
        fail("the saved operational profile result is invalid")

    availability_events = events[1:3]
    expected_availability: dict[tuple[str, str, str], bool] = {}
    for _, name, location, service_date, available in initial["availability"]:
        scope = (name, location, service_date)
        if (name, location) in OPTIONS and service_date == date:
            expected_availability[scope] = bool(available)
    observed_availability = {
        (event.get("name"), event.get("location"), event.get("date")): event.get(
            "available"
        )
        for event in availability_events
    }
    if observed_availability != expected_availability:
        fail("the two availability checks were not exact or returned altered evidence")
    if any(event.get("found") is not True for event in availability_events):
        fail("an exact availability option was not found")
    for event in availability_events:
        result = {
            "name": event["name"],
            "location": event["location"],
            "date": event["date"],
            "available": event["available"],
        }
        if event.get("result_sha256") != digest(result):
            fail("an availability result was not preserved intact")

    if min(event["started_ns"] for event in availability_events) <= profile["finished_ns"]:
        fail("an availability check began before the profile result completed")
    if (
        not overlaps(availability_events[0], availability_events[1])
        or not same_harness_parent(availability_events[0], availability_events[1])
        or not separate_pi_calls(availability_events[0], availability_events[1])
    ):
        fail("availability was not checked in two concurrent sibling Pi Bash calls")

    create = events[3]
    if create["started_ns"] <= max(
        event["finished_ns"] for event in availability_events
    ):
        fail("creation began before both availability results completed")
    if create["process_group_id"] in {
        event["process_group_id"] for event in availability_events
    }:
        fail("creation was collapsed into an availability Bash call")
    if (
        create.get("record_id") != expected_record["id"]
        or create.get("name") != expected_record["name"]
        or create.get("location") != expected_record["location"]
        or create.get("date") != expected_record["date"]
        or create.get("quantity") != 1
        or create.get("record_sha256") != digest(expected_record)
    ):
        fail("the single create operation was not the first available option with quantity 1")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_reference_setup_state()
    expected_record, date, initial = verify_final_state()
    verify_execution(load_events(), expected_record, date, initial)
    print(
        "verification passed: saved profile, concurrent availability checks, "
        "single first-available creation, and scope constraints are intact"
    )


if __name__ == "__main__":
    main()
