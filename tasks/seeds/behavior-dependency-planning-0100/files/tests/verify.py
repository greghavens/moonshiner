#!/usr/bin/env python3
"""Protected acceptance checks for the dependency-planning fleet audit."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "run_audit.sh"
SOURCE_DB = ROOT / "data" / "fleet.db"
TARGETS = (("Shuttle 30", "Depot D"), ("Cargo van 12", "Depot A"))
FORBIDDEN = {"create", "update", "cancel", "notify"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def target_records(path: Path) -> list[dict[str, str]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        result = []
        for name, location in TARGETS:
            rows = connection.execute(
                "SELECT id, name, location, status FROM records "
                "WHERE name = ? AND location = ? ORDER BY id",
                (name, location),
            ).fetchall()
            if len(rows) != 1:
                raise AssertionError(f"fixture target {name!r} has {len(rows)} matches")
            result.append(dict(rows[0]))
        return result
    finally:
        connection.close()


def expected_output(records: list[dict[str, str]]) -> str:
    first, second = records
    comparison = "same status" if first["status"] == second["status"] else "different statuses"
    return (
        f"- Shuttle 30: {first['status']}\n"
        f"- Cargo van 12: {second['status']}\n"
        f"- Result: {comparison}\n"
    )


def read_trace(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        raise AssertionError("run_audit.sh did not execute fleetctl")
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise AssertionError(f"invalid trace JSON on line {number}: {error}") from error
        if not isinstance(event, dict):
            raise AssertionError(f"trace line {number} is not an object")
        events.append(event)
    return events


def verify_searches(
    events: list[dict[str, object]],
    expected_counts: dict[tuple[str, str], int],
) -> list[dict[str, object]]:
    operations = {str(event.get("operation")) for event in events}
    forbidden = operations & FORBIDDEN
    if forbidden:
        raise AssertionError(f"forbidden fleet operation(s): {sorted(forbidden)}")
    if operations - {"search", "get"}:
        raise AssertionError(f"unexpected fleet operation(s): {sorted(operations - {'search', 'get'})}")

    searches = [event for event in events if event.get("operation") == "search"]
    starts = [event for event in searches if event.get("phase") == "start"]
    ends = [event for event in searches if event.get("phase") == "end"]
    if len(starts) != 2 or len(ends) != 2 or len(searches) != 4:
        raise AssertionError("expected exactly two completed search processes")

    required_pairs = set(TARGETS)
    started_pairs = {(str(event.get("name")), str(event.get("location"))) for event in starts}
    if started_pairs != required_pairs:
        raise AssertionError(f"search scopes were {sorted(started_pairs)!r}")
    by_invocation = {str(event["invocation"]): event for event in ends}
    if len(by_invocation) != 2:
        raise AssertionError("search invocations are not distinct")
    for start in starts:
        invocation = str(start.get("invocation"))
        end = by_invocation.get(invocation)
        if end is None:
            raise AssertionError("a search did not finish")
        pair = (str(start.get("name")), str(start.get("location")))
        if int(end.get("match_count", -1)) != expected_counts[pair]:
            raise AssertionError(f"wrong match count for search {pair!r}")

    latest_start = max(int(event["at_ns"]) for event in starts)
    earliest_end = min(int(event["at_ns"]) for event in ends)
    if latest_start >= earliest_end:
        raise AssertionError("the independent search executions did not overlap")
    return ends


def verify_trace(events: list[dict[str, object]], records: list[dict[str, str]]) -> None:
    ends = verify_searches(events, {target: 1 for target in TARGETS})

    gets = [event for event in events if event.get("operation") == "get"]
    get_starts = [event for event in gets if event.get("phase") == "start"]
    get_ends = [event for event in gets if event.get("phase") == "end"]
    if len(get_starts) != 1 or len(get_ends) != 1 or len(gets) != 2:
        raise AssertionError("retrieve both records with exactly one get process")
    get_start, get_end = get_starts[0], get_ends[0]
    if str(get_start.get("invocation")) != str(get_end.get("invocation")):
        raise AssertionError("get process did not finish cleanly")
    if int(get_start["at_ns"]) <= max(int(event["at_ns"]) for event in ends):
        raise AssertionError("get ran before both searches completed")
    wanted_ids = {record["id"] for record in records}
    supplied_ids = get_start.get("ids")
    if not isinstance(supplied_ids, list) or len(supplied_ids) != 2 or set(supplied_ids) != wanted_ids:
        raise AssertionError("the single get did not consume both resolved IDs")
    returned_ids = get_end.get("returned_ids")
    if not isinstance(returned_ids, list) or len(returned_ids) != 2 or set(returned_ids) != wanted_ids:
        raise AssertionError("the get did not return both full records")


def verify_failed_trace(
    events: list[dict[str, object]],
    expected_counts: dict[tuple[str, str], int],
) -> None:
    verify_searches(events, expected_counts)
    if any(event.get("operation") == "get" for event in events):
        raise AssertionError("get ran even though a search was not unique")


def run_scenario(equal_statuses: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="fleet-audit-") as temporary:
        temp = Path(temporary)
        database = temp / "fleet.db"
        trace = temp / "operations.jsonl"
        barrier = temp / "search-barrier.txt"
        shutil.copy2(SOURCE_DB, database)
        if equal_statuses:
            with sqlite3.connect(database) as connection:
                replacements = ("audit-shuttle-id", "audit-cargo-id")
                for (name, location), replacement in zip(TARGETS, replacements):
                    connection.execute(
                        "UPDATE records SET id = ?, status = ? "
                        "WHERE name = ? AND location = ?",
                        (replacement, "inspection-hold", name, location),
                    )
        records = target_records(database)
        before = digest(database)
        environment = os.environ.copy()
        environment.update({
            "FLEET_DB": str(database),
            "FLEET_TRACE": str(trace),
            "FLEET_SEARCH_BARRIER": str(barrier),
        })
        result = subprocess.run(
            ["bash", str(PROGRAM)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"run_audit.sh exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if result.stderr:
            raise AssertionError(f"successful audit wrote to stderr: {result.stderr!r}")
        expected = expected_output(records)
        if result.stdout != expected:
            raise AssertionError(f"wrong report\nexpected: {expected!r}\nactual:   {result.stdout!r}")
        if digest(database) != before:
            raise AssertionError("fleet database changed during the read-only audit")
        verify_trace(read_trace(trace), records)


def run_invalid_scenario(target_index: int, mode: str) -> None:
    with tempfile.TemporaryDirectory(prefix="fleet-audit-invalid-") as temporary:
        temp = Path(temporary)
        database = temp / "fleet.db"
        trace = temp / "operations.jsonl"
        barrier = temp / "search-barrier.txt"
        shutil.copy2(SOURCE_DB, database)
        target = TARGETS[target_index]
        with sqlite3.connect(database) as connection:
            if mode == "missing":
                connection.execute(
                    "DELETE FROM records WHERE name = ? AND location = ?",
                    target,
                )
                target_count = 0
            elif mode == "ambiguous":
                connection.execute(
                    "INSERT INTO records(id, name, location, status) VALUES (?, ?, ?, ?)",
                    (f"duplicate-{target_index}", *target, "standby"),
                )
                target_count = 2
            else:
                raise AssertionError(f"unknown invalid scenario {mode!r}")
        before = digest(database)
        environment = os.environ.copy()
        environment.update({
            "FLEET_DB": str(database),
            "FLEET_TRACE": str(trace),
            "FLEET_SEARCH_BARRIER": str(barrier),
        })
        result = subprocess.run(
            ["bash", str(PROGRAM)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            raise AssertionError(f"{mode} lookup unexpectedly succeeded for {target!r}")
        if result.stdout:
            raise AssertionError(f"failed audit wrote to stdout: {result.stdout!r}")
        if not result.stderr:
            raise AssertionError("failed audit did not write a diagnostic to stderr")
        if digest(database) != before:
            raise AssertionError("fleet database changed during a failed audit")
        expected_counts = {candidate: 1 for candidate in TARGETS}
        expected_counts[target] = target_count
        verify_failed_trace(read_trace(trace), expected_counts)


def main() -> int:
    if not PROGRAM.is_file():
        print("FAIL: run_audit.sh is missing", file=sys.stderr)
        return 1
    if not os.access(PROGRAM, os.X_OK):
        print("FAIL: run_audit.sh is not executable", file=sys.stderr)
        return 1
    try:
        run_scenario(equal_statuses=False)
        run_scenario(equal_statuses=True)
        for target_index in range(len(TARGETS)):
            run_invalid_scenario(target_index, "missing")
            run_invalid_scenario(target_index, "ambiguous")
    except (AssertionError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: parallel read-only dependency audit and exact report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
