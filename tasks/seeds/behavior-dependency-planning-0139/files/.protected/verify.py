#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-dependency-planning-0139."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / ".public-services" / "seed.sql"
RUNTIME_DIR = ROOT / ".public-services" / "runtime"
DATABASE_PATH = RUNTIME_DIR / "public-services.sqlite3"
AUDIT_PATH = RUNTIME_DIR / "audit.jsonl"
KEY_PATH = ROOT / ".protected" / "audit.key"
REFERENCE_MARKER = ROOT / ".reference_solution"
OPTIONS = [
    ("Food cart license", "Arvada"),
    ("Block party application", "Wheat Ridge"),
]
EXPECTED_HASHES = {
    "README.md": "bcf7a87ee114c1e5a0ce0d1e3342ad582bc53ae38ec0a17c16e8b8b69ba82176",
    "publicservicesctl": "8d41355cbe4958b3eb507a88fe783ee771fa230ef36a0be90de1e5d08dccf24b",
    ".public-services/seed.sql": "e40eea4f5010048ce39b3ffef128c2dbc68070f8e7f056de90d732f3cce38b32",
    ".protected/audit.key": "146808acfcc2067f9bd1b56d01dfaba7d761277d630b0778fd8b468e05548813",
    ".gitignore": "e6f070af46f21b905ab86da4ec0fceccfda0944930777a6056e40e6d2cf9b2d7",
}
ALLOWED_ROOT_ENTRIES = {
    ".public-services",
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution",
    "README.md",
    "publicservicesctl",
}
ALLOWED_RUNTIME_ENTRIES = {
    "audit.jsonl",
    "public-services.sqlite3",
    "registry.lock",
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
    if not RUNTIME_DIR.is_dir():
        fail("the public-services registry was never executed")
    unexpected_runtime = sorted(
        path.name
        for path in RUNTIME_DIR.iterdir()
        if path.name not in ALLOWED_RUNTIME_ENTRIES
    )
    if unexpected_runtime:
        fail("unexpected public-services runtime artifact: " + ", ".join(unexpected_runtime))


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
        fail("the public-services registry was never executed")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as error:
        fail(f"runtime public-services database is unreadable: {error}")
    if integrity is None or integrity[0] != "ok":
        connection.close()
        fail("runtime public-services database failed its integrity check")
    return connection


def rows(connection: sqlite3.Connection, query: str) -> list[list[Any]]:
    return [list(row) for row in connection.execute(query).fetchall()]


def request_objects(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in connection.execute(
        """
        SELECT id, option_id, name, location, service_date AS date, quantity,
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


def expected_created_record(
    initial: sqlite3.Connection,
) -> tuple[dict[str, Any], str]:
    profile = initial.execute(
        "SELECT default_date FROM profile WHERE singleton = 1"
    ).fetchone()
    if profile is None:
        fail("protected profile fixture is missing")
    date = str(profile["default_date"])
    selected: sqlite3.Row | None = None
    for name, location in OPTIONS:
        row = initial.execute(
            """
            SELECT option_id, name, location, available FROM availability
            WHERE name = ? AND location = ? AND service_date = ?
            """,
            (name, location, date),
        ).fetchone()
        if row is not None and bool(row["available"]):
            selected = row
            break
    if selected is None:
        fail("protected fixture does not exercise the conditional create branch")
    record_id = initial.execute(
        "SELECT value FROM metadata WHERE key = 'created_record_id'"
    ).fetchone()
    if record_id is None:
        fail("protected created-record ID is missing")
    return (
        {
            "id": str(record_id["value"]),
            "option_id": str(selected["option_id"]),
            "name": str(selected["name"]),
            "location": str(selected["location"]),
            "date": date,
            "quantity": 1,
            "status": "submitted",
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
            fail(f"an existing or out-of-scope request changed: {request_id}")
    if current_requests.get(expected_record["id"]) != expected_record:
        fail("the new request is not exactly the first available requested option")
    return expected_record, date, initial


def load_events() -> list[dict[str, Any]]:
    if not AUDIT_PATH.is_file():
        fail("no genuine publicservicesctl execution evidence exists")
    try:
        key = KEY_PATH.read_bytes().strip()
        raw_lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"public-services audit evidence is unreadable: {error}")
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
            fail(f"audit event {index} was not emitted intact by publicservicesctl")
        if event.get("sequence") != index:
            fail("audit sequence is incomplete or reordered")
        for field in (
            "started_ns",
            "finished_ns",
            "parent_pid",
            "parent_start_ticks",
            "process_pid",
            "process_group_id",
            "session_id",
        ):
            if not isinstance(event.get(field), int) or isinstance(event.get(field), bool):
                fail(f"audit event {index} has invalid execution evidence")
        if not isinstance(event.get("parent_executable"), str) or not event[
            "parent_executable"
        ]:
            fail(f"audit event {index} has invalid parent execution evidence")
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
        first["parent_pid"] == second["parent_pid"]
        and first["parent_start_ticks"] == second["parent_start_ticks"]
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
        fail(f"expected exactly four public-services-registry operations, found {len(events)}")
    if [event.get("operation") for event in events] != [
        "profile",
        "availability",
        "availability",
        "create",
    ]:
        fail("operations were missing, out of dependency order, or included an extra action")
    if any(event.get("success") is not True for event in events):
        fail("a required public-services-registry operation did not succeed")
    reference_context = REFERENCE_MARKER.is_file()
    if any(event.get("reference_marker") is not reference_context for event in events):
        fail("execution evidence does not match the active reference context")
    if not reference_context and any(
        event.get("parent_executable") != "node" for event in events
    ):
        fail("a registry operation was not executed directly by Pi's Bash tool")

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
    expected_availability: dict[tuple[str, str, str], tuple[str, bool]] = {}
    for option_id, name, location, service_date, available in initial["availability"]:
        if (name, location) in OPTIONS and service_date == date:
            expected_availability[(name, location, service_date)] = (
                option_id,
                bool(available),
            )
    observed_availability = {
        (event.get("name"), event.get("location"), event.get("date")): (
            event.get("option_id"),
            event.get("available"),
        )
        for event in availability_events
    }
    if observed_availability != expected_availability:
        fail("the two availability checks were not exact or returned altered evidence")
    if any(event.get("found") is not True for event in availability_events):
        fail("an exact availability option was not found")
    for event in availability_events:
        result = {
            "option_id": event["option_id"],
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
        or create.get("option_id") != expected_record["option_id"]
        or create.get("date") != expected_record["date"]
        or create.get("quantity") != 1
        or create.get("record_sha256") != digest(expected_record)
    ):
        fail("the single create operation was not the first available option with quantity 1")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    expected_record, date, initial = verify_final_state()
    verify_execution(load_events(), expected_record, date, initial)
    print(
        "verification passed: saved profile, concurrent availability checks, "
        "single first-available creation, and scope constraints are intact"
    )


if __name__ == "__main__":
    main()
