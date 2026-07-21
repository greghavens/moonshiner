#!/usr/bin/env python3
"""Read-only command-line access to the controlled registrar catalog."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "catalog.sqlite"
TRACE_DIR = ROOT / ".registrar"
TRACE = TRACE_DIR / "audit.json"
LOCK = TRACE_DIR / "audit.lock"
BARRIER_TIMEOUT_SECONDS = 3.0


def action_identity() -> str:
    """Identify the immediate Bash process even when a PID namespace reuses PID 2."""
    parent_pid = os.getppid()
    stat = Path(f"/proc/{parent_pid}/stat").read_text(encoding="utf-8")
    # Fields after the executable name begin at proc(5) field 3; field 22 is
    # the process start time. Combining it with the namespaced PID distinguishes
    # successive Bash tool executions while both background children agree.
    start_ticks = stat.rsplit(")", 1)[1].split()[19]
    return f"{parent_pid}:{start_ticks}"


@contextmanager
def locked_trace() -> Iterator[dict[str, Any]]:
    TRACE_DIR.mkdir(exist_ok=True)
    with LOCK.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if TRACE.exists():
            data = json.loads(TRACE.read_text(encoding="utf-8"))
        else:
            data = {"schema_version": 1, "calls": []}
        yield data
        temporary = TRACE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(TRACE)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def begin_call(operation: str, arguments: dict[str, str]) -> int:
    with locked_trace() as trace:
        call_id = len(trace["calls"]) + 1
        trace["calls"].append(
            {
                "arguments": arguments,
                "action_identity": action_identity(),
                "call_id": call_id,
                "finished_ns": None,
                "operation": operation,
                "outcome": None,
                "result": None,
                "started_ns": time.monotonic_ns(),
            }
        )
    return call_id


def finish_call(call_id: int, outcome: str, result: Any) -> None:
    with locked_trace() as trace:
        call = next(item for item in trace["calls"] if item["call_id"] == call_id)
        call["finished_ns"] = time.monotonic_ns()
        call["outcome"] = outcome
        call["result"] = result


def wait_for_parallel_peer(call_id: int, operation: str) -> bool:
    deadline = time.monotonic() + BARRIER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        with locked_trace() as trace:
            own = next(item for item in trace["calls"] if item["call_id"] == call_id)
            peers = [
                item
                for item in trace["calls"]
                if item["call_id"] != call_id
                and item["operation"] == operation
                and item["finished_ns"] is None
                and abs(item["started_ns"] - own["started_ns"])
                <= int(BARRIER_TIMEOUT_SECONDS * 1_000_000_000)
            ]
        if peers:
            return True
        time.sleep(0.02)
    return False


def catalog_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def search(name: str, location: str) -> int:
    call_id = begin_call("search", {"location": location, "name": name})
    if not wait_for_parallel_peer(call_id, "search"):
        result = {"error": "search requires a concurrently issued peer search"}
        finish_call(call_id, "rejected", result)
        print(json.dumps(result), file=sys.stderr)
        return 2

    with catalog_connection() as connection:
        rows = connection.execute(
            """
            SELECT stable_id, name, location
            FROM courses
            WHERE name = ? AND location = ?
            ORDER BY stable_id
            """,
            (name, location),
        ).fetchall()
    result = {
        "location": location,
        "match_count": len(rows),
        "matches": [dict(row) for row in rows],
        "name": name,
    }
    time.sleep(0.12)
    finish_call(call_id, "ok", result)
    print(json.dumps(result, sort_keys=True))
    return 0


def uniquely_searched(stable_id: str) -> bool:
    with locked_trace() as trace:
        completed_searches = [
            item
            for item in trace["calls"]
            if item["operation"] == "search"
            and item["outcome"] == "ok"
            and item["finished_ns"] is not None
        ]
    for item in completed_searches:
        result = item["result"]
        matches = result.get("matches", [])
        if len(matches) == 1 and matches[0].get("stable_id") == stable_id:
            return True
    return False


def get(stable_id: str) -> int:
    call_id = begin_call("get", {"stable_id": stable_id})
    if not uniquely_searched(stable_id):
        result = {"error": "get requires one stable ID from a completed unique search"}
        finish_call(call_id, "rejected", result)
        print(json.dumps(result), file=sys.stderr)
        return 2
    if not wait_for_parallel_peer(call_id, "get"):
        result = {"error": "get requires a concurrently issued peer get"}
        finish_call(call_id, "rejected", result)
        print(json.dumps(result), file=sys.stderr)
        return 2

    with catalog_connection() as connection:
        row = connection.execute(
            """
            SELECT stable_id, name, location, status, course_date
            FROM courses
            WHERE stable_id = ?
            """,
            (stable_id,),
        ).fetchone()
    result = dict(row) if row is not None else None
    time.sleep(0.12)
    finish_call(call_id, "ok", result)
    print(json.dumps(result, sort_keys=True))
    return 0


def forbidden(operation: str, values: dict[str, str]) -> int:
    call_id = begin_call(operation, values)
    result = {"error": f"{operation} is outside this read-only audit"}
    finish_call(call_id, "rejected", result)
    print(json.dumps(result), file=sys.stderr)
    return 3


def show_help(command: argparse.ArgumentParser) -> int:
    """Display and audit the top-level interface requested by the task."""
    call_id = begin_call("help", {})
    output = command.format_help()
    finish_call(call_id, "ok", {"displayed": True})
    print(output, end="")
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Query the sandboxed registrar through audited operations."
    )
    subcommands = command.add_subparsers(dest="operation", required=True)

    search_parser = subcommands.add_parser("search", help="search by exact name and location")
    search_parser.add_argument("--name", required=True)
    search_parser.add_argument("--location", required=True)

    get_parser = subcommands.add_parser("get", help="retrieve one record by stable ID")
    get_parser.add_argument("--id", required=True, dest="stable_id")

    subcommands.add_parser("list", help="list the collection (not permitted for this audit)")
    subcommands.add_parser("preferences", help="read saved preferences (not permitted)")

    availability_parser = subcommands.add_parser("availability", help="check availability")
    availability_parser.add_argument("--id", default="", dest="stable_id")

    for name in ("create", "update", "cancel", "notify"):
        mutation = subcommands.add_parser(name, help=f"{name} a registrar record")
        mutation.add_argument("--id", default="", dest="stable_id")
    return command


def main() -> int:
    command = parser()
    if sys.argv[1:] == ["--help"]:
        return show_help(command)
    arguments = command.parse_args()
    if arguments.operation == "search":
        return search(arguments.name, arguments.location)
    if arguments.operation == "get":
        return get(arguments.stable_id)
    values = {
        key: str(value)
        for key, value in vars(arguments).items()
        if key != "operation" and value
    }
    return forbidden(arguments.operation, values)


if __name__ == "__main__":
    raise SystemExit(main())
