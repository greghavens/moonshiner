#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "order-audit.jsonl"
DATABASE_PATH = ROOT / ".orders" / "runtime" / "orders.sqlite3"
REPORT_PATH = ROOT / "order_review.md"
AUDIT_KEY = b"moonshiner-pi-order-audit-0226-v1"

EXPECTED_HASHES = {
    "order-registry": "73e29b0a5d745464e8c35c4f34156aca82089d592500ca44e46aac7b575ab9b6",
    ".orders/seed.sql": "ea9f6fe9d30a694f2a976c63ed84503f39498937e4eee4dca08695b2ff51b04d",
    ".harness/reference_setup.py": "a457bd5507a086b07c5d4456def0545577b6e3938a27559e7dda3a9620b9c2ef",
    "README.md": "2372579e8cda17fb9eb4fe68e3872d8cbb56478035e094bab17a0edb96ac05c9",
    ".gitignore": "d84ba58bc09bb0ede5fa913e199518c4212eb11012f904c08a89971bd464d58d",
}
EXPECTED_DATABASE_DIGEST = "3e9347d7a518e21feffd376479a3670b0b368dfee560d9ee729b5975956ac0a6"
REFERENCE_MARKER_DIGEST = "b5495d4b30b3deb714c93259c0a117fd03ff8b9cf2343d7f05090806f9725a71"
EXPECTED_SEARCHES = {
    ("Lab glassware replenishment", "Madison"),
    ("Conference lanyard order", "Denver"),
}
EXPECTED_REPORT = (
    "Lab glassware replenishment in Madison has status awaiting-stock and date 2026-07-20.\n"
    "Conference lanyard order in Denver has status shipped and date 2026-07-21.\n"
    "The returned statuses differ; the returned dates differ.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".orders",
    ".reference_solution",
    "README.md",
    "order-registry",
    "order_review.md",
}
SHELL_LAUNCHERS = {"ash", "bash", "busybox", "dash", "fish", "ksh", "mksh", "sh", "zsh"}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected order-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))

    marker = ROOT / ".reference_solution"
    if marker.exists() and (
        not marker.is_file() or file_sha256(marker) != REFERENCE_MARKER_DIGEST
    ):
        fail("unexpected or altered reference marker")

    allowed_nested_entries = {
        ROOT / ".orders": {"runtime", "seed.sql"},
        ROOT / ".orders" / "runtime": {"initialize.lock", "orders.sqlite3"},
        ROOT / ".harness": {"reference_setup.py", "runtime", "verify.py"},
        ROOT / ".harness" / "runtime": {"order-audit.jsonl"},
    }
    for directory, allowed in allowed_nested_entries.items():
        if not directory.exists():
            continue
        if not directory.is_dir():
            fail(f"managed path is not a directory: {directory.relative_to(ROOT)}")
        unexpected = sorted(path.name for path in directory.iterdir() if path.name not in allowed)
        if unexpected:
            fail(
                f"unexpected scratch artifact under {directory.relative_to(ROOT)}: "
                + ", ".join(unexpected)
            )


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite order registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, city, status, record_date, supplier,
                       amount_cents, description, lifecycle
                FROM order_records ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"order registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("order registry database integrity check failed")
    snapshot = {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "order registry state changed; targets, similarly named, related, "
            "archived, other orders, and notifications must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine order-registry execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"audit event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by the registry")
        for field in (
            "start_ns",
            "end_ns",
            "process_pid",
            "process_group_id",
            "session_id",
            "parent_pid",
        ):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event.get("parent_executable") in {None, "", "unavailable"} or not isinstance(
            event.get("parent_executable"), str
        ):
            fail(f"audit event {number} lacks its genuine launcher evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        events.append(event)
    return sorted(events, key=lambda item: item["start_ns"])


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict, second: dict) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def separate_tool_calls(first: dict, second: dict) -> bool:
    return (
        first["process_group_id"] != second["process_group_id"]
        and first["process_pid"] == first["process_group_id"]
        and second["process_pid"] == second["process_group_id"]
        and first["session_id"] == first["process_pid"]
        and second["session_id"] == second["process_pid"]
    )


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("a create, update, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required order-registry operation must succeed")
    if any(Path(event["parent_executable"]).name in SHELL_LAUNCHERS for event in events):
        fail("registry operations were backgrounded inside a shell instead of sibling Pi Bash calls")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    observed_searches = {(event.get("name"), event.get("city")) for event in searches}
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-city searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both order lookups must resolve to exactly one current stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        or not event["result_ids"][0]
        for event in searches
    ):
        fail("a search did not return one auditable stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not concurrent sibling Pi Bash calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = {event["result_ids"][0] for event in searches}
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current order record was not retrieved")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the retrievals were not concurrent sibling Pi Bash calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in gets
    }:
        fail("search and retrieval stages reused a Bash call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("order_review.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("order_review.md is not the exact grounded three-line comparison")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded order comparison and genuine Pi registry execution "
        "satisfy every dependency, concurrency, scope, and read-only constraint"
    )


if __name__ == "__main__":
    main()
