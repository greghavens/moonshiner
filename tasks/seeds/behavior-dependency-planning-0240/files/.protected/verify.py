#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi fleet dependency task."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / ".fleet" / "seed.sql"
DATABASE_PATH = ROOT / ".fleet" / "runtime" / "fleet.sqlite3"
AUDIT_PATH = ROOT / ".protected" / "runtime" / "fleet-audit.jsonl"
REPORT_PATH = ROOT / "fleet_handoff.md"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
AUDIT_KEY = bytes.fromhex(AUDIT_KEY_PATH.read_text(encoding="utf-8").strip())

EXPECTED_HASHES = {
    "README.md": "83b7e246dacb48354c2831462e200516e6ba12df0ae8648f1857f3e067989684",
    ".gitignore": "82046b1977c2dee5113b892066e60c47239c5962606a51b91d667f7bed1c397a",
    ".fleet/seed.sql": "f639403eb421aa700b15474b881ace290438935988df69bc8219768b2fcd72e2",
    ".protected/audit.key": "caebc08f03f137fe13d37ea0d243722df2b65046fcc3f5d0c83fedc49f9cf551",
    "fleetctl": "09123bcbea93c5c0032acd9a06dd86b84e6410522bc7eb0512bf39ff81dd406f",
}
REQUESTED_LOOKUPS = (
    ("Bus 14 museum charter", "Depot E"),
    ("EV 6 facilities inspection", "Depot F"),
)
ALLOWED_OPERATIONS = {"help", "search", "get"}
REQUIRED_RECORD_FIELDS = {
    "id",
    "name",
    "location",
    "status",
    "date",
    "vehicle_type",
    "coordinator",
    "notes",
    "lifecycle",
}
ALLOWED_ROOT_ENTRIES = {
    ".fleet",
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution",
    ".sandbox-home",
    "README.md",
    "fleet_handoff.md",
    "fleetctl",
}
DIRECT_LAUNCH_TOLERANCE_NS = 750_000_000
REFERENCE_MARKER_SHA256 = "f3fdb9ea705bf6f882a2fd19ecfcb260c710c5dceb5ef89b321fc9a733c95e09"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected fleet input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at sandbox root: " + ", ".join(unexpected))

    marker = ROOT / ".reference_solution"
    if marker.exists() and (
        not marker.is_file() or file_sha256(marker) != REFERENCE_MARKER_SHA256
    ):
        fail("unexpected reference marker content")

    fleet_entries = {path.name for path in (ROOT / ".fleet").iterdir()}
    if fleet_entries != {"seed.sql", "runtime"}:
        fail("unexpected or missing managed fleet entry")
    runtime_entries = {
        path.name for path in (ROOT / ".fleet" / "runtime").iterdir()
    }
    if not {"fleet.sqlite3", "initialize.lock"}.issubset(runtime_entries) or not runtime_entries.issubset(
        {
            "fleet.sqlite3",
            "fleet.sqlite3-shm",
            "fleet.sqlite3-wal",
            "initialize.lock",
        }
    ):
        fail("unexpected or missing fleet runtime artifact")

    protected_entries = {path.name for path in (ROOT / ".protected").iterdir()}
    if protected_entries != {"audit.key", "reference_setup.py", "runtime", "verify.py"}:
        fail("unexpected or missing protected fleet entry")
    protected_runtime = {
        path.name for path in (ROOT / ".protected" / "runtime").iterdir()
    }
    if protected_runtime != {"fleet-audit.jsonl"}:
        fail("unexpected or missing protected execution evidence")


def database_snapshot(database: sqlite3.Connection) -> dict[str, list[list[object]]]:
    vehicles = database.execute(
        """
        SELECT id, name, location, status, record_date, vehicle_type,
               coordinator, notes, lifecycle
          FROM vehicles
         ORDER BY id
        """
    ).fetchall()
    notifications = database.execute(
        """
        SELECT notification_id, vehicle_id, message
          FROM notifications
         ORDER BY notification_id
        """
    ).fetchall()
    return {
        "vehicles": [list(row) for row in vehicles],
        "notifications": [list(row) for row in notifications],
    }


def records_by_id(database: sqlite3.Connection) -> dict[str, dict[str, str]]:
    rows = database.execute(
        """
        SELECT id, name, location, status, record_date, vehicle_type,
               coordinator, notes, lifecycle
          FROM vehicles
         ORDER BY id
        """
    ).fetchall()
    return {
        row[0]: {
            "id": row[0],
            "name": row[1],
            "location": row[2],
            "status": row[3],
            "date": row[4],
            "vehicle_type": row[5],
            "coordinator": row[6],
            "notes": row[7],
            "lifecycle": row[8],
        }
        for row in rows
    }


def verify_database_state() -> dict[str, dict[str, str]]:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite fleet register was never opened")
    actual = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    expected = sqlite3.connect(":memory:")
    try:
        integrity = actual.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            fail("fleet register database integrity check failed")
        expected.executescript(SEED_PATH.read_text(encoding="utf-8"))
        expected.commit()
        if database_snapshot(actual) != database_snapshot(expected):
            fail(
                "fleet state changed; requested, related, draft, other, and notification state must remain untouched"
            )
        records = records_by_id(actual)
    except sqlite3.DatabaseError as exc:
        fail(f"fleet register database is unreadable: {exc}")
    finally:
        actual.close()
        expected.close()
    return records


def load_events() -> list[dict[str, object]]:
    if not AUDIT_PATH.is_file():
        fail("no genuine fleetctl execution evidence was produced")
    events: list[dict[str, object]] = []
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            signed = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"fleet audit event {number} is not valid JSON")
        if not isinstance(signed, dict):
            fail(f"fleet audit event {number} is not an object")
        event = dict(signed)
        signature = event.pop("signature", None)
        expected_signature = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature,
            expected_signature,
        ):
            fail(f"fleet audit event {number} was not emitted intact by fleetctl")
        for field in (
            "start_ns",
            "end_ns",
            "process_pid",
            "process_group_id",
            "session_id",
            "parent_pid",
        ):
            if not isinstance(event.get(field), int):
                fail(f"fleet audit event {number} has invalid process evidence")
        if int(event["start_ns"]) >= int(event["end_ns"]):
            fail(f"fleet audit event {number} has an invalid execution interval")
        if not isinstance(event.get("event_id"), str) or not event["event_id"]:
            fail(f"fleet audit event {number} has no event ID")
        events.append(event)
    if len({event["event_id"] for event in events}) != len(events):
        fail("fleet audit event IDs are not unique")
    return sorted(events, key=lambda event: (int(event["start_ns"]), str(event["event_id"])))


def overlaps(first: dict[str, object], second: dict[str, object]) -> bool:
    return max(int(first["start_ns"]), int(second["start_ns"])) < min(
        int(first["end_ns"]),
        int(second["end_ns"]),
    )


def same_harness_parent(first: dict[str, object], second: dict[str, object]) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def separate_tool_calls(first: dict[str, object], second: dict[str, object]) -> bool:
    return (
        first["process_group_id"] != second["process_group_id"]
        and first["process_pid"] == first["process_group_id"]
        and second["process_pid"] == second["process_group_id"]
        and first["session_id"] == first["process_pid"]
        and second["session_id"] == second["process_pid"]
    )


def launched_directly(event: dict[str, object]) -> bool:
    ticks = event.get("process_start_ticks")
    if not isinstance(ticks, str) or not ticks.isdigit():
        return False
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    process_start_ns = int(ticks) * 1_000_000_000 // ticks_per_second
    launch_age_ns = int(event["start_ns"]) - process_start_ns
    return 0 <= launch_age_ns <= DIRECT_LAUNCH_TOLERANCE_NS


def exact_targets(records: dict[str, dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    targets: dict[tuple[str, str], dict[str, str]] = {}
    for lookup in REQUESTED_LOOKUPS:
        matches = [
            record
            for record in records.values()
            if (record["name"], record["location"]) == lookup
            and record["lifecycle"] == "current"
        ]
        if len(matches) != 1:
            fail("protected fleet environment does not contain one current exact target")
        targets[lookup] = matches[0]
    return targets


def verify_execution(
    events: list[dict[str, object]],
    records: dict[str, dict[str, str]],
) -> dict[tuple[str, str], str]:
    if not events:
        fail("no fleetctl execution evidence was produced")
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an update, cancel, notify, or out-of-scope fleet operation was executed")
    if any(event.get("success") is not True for event in events):
        fail("every required fleet operation must complete successfully")
    if any(not launched_directly(event) for event in events):
        fail("a fleet operation was not launched as a direct executable process")

    help_event = events[0]
    if (
        help_event.get("operation") != "help"
        or help_event.get("arguments") != ["--help"]
    ):
        fail("./fleetctl --help must be run before every fleet-data operation")
    data_events = [
        event for event in events if event.get("operation") in {"search", "get"}
    ]
    if len(data_events) != 4:
        fail("expected exactly two fleet searches followed by exactly two retrievals")
    searches = data_events[:2]
    gets = data_events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first fleet-data action must contain only both exact searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the later fleet-data action must contain only both retrievals")

    targets = exact_targets(records)
    searches_by_lookup = {
        (str(event.get("name")), str(event.get("location"))): event
        for event in searches
    }
    if set(searches_by_lookup) != set(REQUESTED_LOOKUPS):
        fail("the two required exact name-and-depot searches were not run")
    if not (
        overlaps(searches[0], searches[1])
        and same_harness_parent(searches[0], searches[1])
        and separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not concurrent sibling Pi Bash tool calls")

    ids_by_lookup: dict[tuple[str, str], str] = {}
    for lookup, event in searches_by_lookup.items():
        record = targets[lookup]
        expected_output = {
            "matches": [
                {
                    "id": record["id"],
                    "name": record["name"],
                    "location": record["location"],
                }
            ]
        }
        if (
            event.get("result_count") != 1
            or event.get("result_ids") != [record["id"]]
            or event.get("result_digest") != digest(expected_output)
        ):
            fail("an exact search did not return its one authentic stable ID")
        ids_by_lookup[lookup] = record["id"]

    if min(int(event["start_ns"]) for event in gets) <= max(
        int(event["end_ns"]) for event in searches
    ):
        fail("a retrieval began before both search responses were available")
    returned_ids = set(ids_by_lookup.values())
    if {event.get("vehicle_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by their searches")
    for event in gets:
        vehicle_id = str(event.get("vehicle_id"))
        record = records.get(vehicle_id)
        if record is None or set(record) != REQUIRED_RECORD_FIELDS:
            fail("a retrieval did not resolve to a complete managed fleet record")
        if (
            event.get("result_count") != 1
            or event.get("field_count") != len(REQUIRED_RECORD_FIELDS)
            or event.get("result_digest") != digest({"record": record})
        ):
            fail("a retrieval did not return the authentic complete vehicle record")
    if not (
        overlaps(gets[0], gets[1])
        and same_harness_parent(gets[0], gets[1])
        and separate_tool_calls(gets[0], gets[1])
    ):
        fail("the retrievals were not concurrent sibling Pi Bash tool calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in gets
    }:
        fail("search and retrieval stages reused a Bash tool call")
    return ids_by_lookup


def date_relation(first: str, second: str) -> str:
    if first == second:
        return "the same as"
    return "earlier than" if first < second else "later than"


def verify_report(
    records: dict[str, dict[str, str]],
    ids_by_lookup: dict[tuple[str, str], str],
) -> None:
    ordered = [records[ids_by_lookup[lookup]] for lookup in REQUESTED_LOOKUPS]
    first, second = ordered
    status_relation = "match" if first["status"] == second["status"] else "differ"
    expected = (
        f'- {first["name"]} | {first["location"]} | ID {first["id"]} | '
        f'status {first["status"]} | date {first["date"]}\n'
        f'- {second["name"]} | {second["location"]} | ID {second["id"]} | '
        f'status {second["status"]} | date {second["date"]}\n'
        f'- Comparison | statuses {status_relation} '
        f'({first["status"]} vs {second["status"]}) | {first["name"]} is dated '
        f'{date_relation(first["date"], second["date"])} {second["name"]} '
        f'({first["date"]} vs {second["date"]})\n'
    )
    if not REPORT_PATH.is_file():
        fail("fleet_handoff.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected:
        fail("fleet_handoff.md is not the exact grounded two-record comparison")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    records = verify_database_state()
    ids_by_lookup = verify_execution(load_events(), records)
    verify_report(records, ids_by_lookup)
    print(
        "PASS: genuine Pi fleet execution satisfies concurrency, dependency, provenance, scope, grounding, and read-only constraints"
    )


if __name__ == "__main__":
    main()
