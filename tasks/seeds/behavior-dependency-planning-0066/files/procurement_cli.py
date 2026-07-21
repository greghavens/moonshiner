#!/usr/bin/env python3
"""Executable, read-only procurement record client used by the task."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable


HERE = Path(__file__).resolve().parent
READ_ONLY_ACTIONS = {"batch-search", "batch-get"}
FORBIDDEN_ACTIONS = {
    "list",
    "preferences",
    "availability",
    "create",
    "update",
    "cancel",
    "notify",
}


def data_path() -> Path:
    return Path(os.environ.get("PROCUREMENT_DATA", HERE / "orders.csv"))


def trace_path() -> Path:
    return Path(os.environ.get("PROCUREMENT_TRACE", HERE / ".procurement-trace.jsonl"))


def load_records() -> list[dict[str, str]]:
    with data_path().open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parallel_map(items: list[object], operation: Callable[[object], object]) -> list[dict[str, object]]:
    """Run actual record operations concurrently and retain timing evidence."""
    if not items:
        return []
    ready = threading.Barrier(len(items)) if len(items) > 1 else None
    finished = threading.Barrier(len(items)) if len(items) > 1 else None

    def worker(item: object) -> dict[str, object]:
        if ready is not None:
            ready.wait()
        started_ns = time.monotonic_ns()
        result = operation(item)
        if finished is not None:
            finished.wait()
        ended_ns = time.monotonic_ns()
        return {
            "input": item,
            "started_ns": started_ns,
            "ended_ns": ended_ns,
            "result": result,
        }

    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        futures = [pool.submit(worker, item) for item in items]
        return [future.result() for future in futures]


def append_event(action: str, operations: list[dict[str, object]]) -> None:
    path = trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "action": action,
        "process_id": os.getpid(),
        "parent_process_id": os.getppid(),
        "operations": operations,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def do_batch_search(queries: list[list[str]]) -> int:
    normalized = [{"name": value[0], "location": value[1]} for value in queries]

    def search(raw: object) -> list[str]:
        query = dict(raw)  # type: ignore[arg-type]
        return [
            record["stable_id"]
            for record in load_records()
            if record["name"] == query["name"] and record["location"] == query["location"]
        ]

    operations = parallel_map(normalized, search)
    append_event("batch-search", operations)
    payload = {
        "searches": [
            {**dict(operation["input"]), "stable_ids": operation["result"]}
            for operation in operations
        ]
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def do_batch_get(stable_ids: list[str]) -> int:
    def get(raw: object) -> dict[str, str] | None:
        stable_id = str(raw)
        return next(
            (record for record in load_records() if record["stable_id"] == stable_id),
            None,
        )

    operations = parallel_map(list(stable_ids), get)
    append_event("batch-get", operations)
    print(json.dumps({"records": [operation["result"] for operation in operations]}, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Read-only client for the sandboxed procurement record store."
    )
    commands = root.add_subparsers(dest="action", required=True)

    search = commands.add_parser(
        "batch-search",
        help="search two independent name/location branches concurrently",
    )
    search.add_argument(
        "--query",
        action="append",
        nargs=2,
        metavar=("NAME", "LOCATION"),
        required=True,
        help="exact name and location; provide this option exactly twice",
    )

    get = commands.add_parser(
        "batch-get",
        help="retrieve one or more stable IDs concurrently in one batch",
    )
    get.add_argument("--id", action="append", required=True, dest="stable_ids")

    for name in sorted(FORBIDDEN_ACTIONS):
        commands.add_parser(name, help=f"out-of-scope {name} operation")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.action == "batch-search":
        if len(args.query) != 2:
            parser().error("batch-search requires exactly two --query values")
        return do_batch_search(args.query)
    if args.action == "batch-get":
        if len(args.stable_ids) not in {1, 2}:
            parser().error("batch-get accepts one or two --id values")
        return do_batch_get(args.stable_ids)
    append_event(args.action, [])
    print(f"operation '{args.action}' is outside this read-only audit", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
