#!/usr/bin/env python3
"""Protected acceptance verification for the procurement audit runner."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
RUNNER = HERE / "audit_runner.py"
CLIENT = HERE / "procurement_cli.py"
DATA = HERE / "orders.csv"
TARGETS = [
    ("Winter Field Jacket Order", "North Depot"),
    ("Safety Boot Order", "South Depot"),
]
ALLOWED_ACTIONS = {"batch-search", "batch-get"}
RUNNER_GUARD = r'''\
import os
import sys


if os.path.basename(sys.argv[0]) == "audit_runner.py":
    protected_data = os.path.realpath(os.environ["PROCUREMENT_DATA"])
    protected_trace = os.path.realpath(os.environ["PROCUREMENT_TRACE"])

    def protect_client_files(event, arguments):
        if event != "open" or not arguments:
            return
        try:
            opened = os.path.realpath(os.fspath(arguments[0]))
        except TypeError:
            return
        if opened == protected_data:
            raise PermissionError("audit_runner.py must obtain records through procurement_cli.py")
        if opened != protected_trace:
            return
        mode = arguments[1] if len(arguments) > 1 else None
        flags = arguments[2] if len(arguments) > 2 else 0
        writes = (isinstance(mode, str) and any(marker in mode for marker in "wax+")) or (
            isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
        )
        if writes:
            raise PermissionError("audit_runner.py must not manufacture the client execution trace")

    sys.addaudithook(protect_client_files)
'''


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["stable_id", "name", "location", "order_date", "status"])
        writer.writeheader()
        writer.writerows(rows)


def overlaps(operations: list[dict[str, object]]) -> bool:
    if len(operations) < 2:
        return True
    latest_start = max(int(operation["started_ns"]) for operation in operations)
    earliest_end = min(int(operation["ended_ns"]) for operation in operations)
    return latest_start <= earliest_end


def expected_report(rows: list[dict[str, str]]) -> list[str]:
    matches = [
        [row for row in rows if row["name"] == name and row["location"] == location]
        for name, location in TARGETS
    ]
    resolved = [branch[0] for branch in matches if len(branch) == 1]
    lines = [
        " | ".join(
            [record["order_date"], record["stable_id"], record["name"], record["location"], record["status"]]
        )
        for record in sorted(resolved, key=lambda record: (record["order_date"], record["stable_id"]))
    ]
    unresolved = [TARGETS[index] for index, branch in enumerate(matches) if len(branch) != 1]
    if unresolved:
        lines.extend(f"UNRESOLVED | {name} | {location}" for name, location in unresolved)
        lines.append("Status comparison unavailable because one or more orders are unresolved.")
    else:
        ordered = sorted(resolved, key=lambda record: (record["order_date"], record["stable_id"]))
        lines.append(f"The earlier order is {ordered[0]['status']}; the later order is {ordered[1]['status']}.")
    return lines


def verify_trace(events: list[dict[str, object]], rows: list[dict[str, str]]) -> None:
    require(events, "runner produced no client execution trace")
    require(all(event.get("action") in ALLOWED_ACTIONS for event in events), "an out-of-scope operation was executed")
    require(
        all(
            isinstance(event.get("process_id"), int)
            and isinstance(event.get("parent_process_id"), int)
            and event["parent_process_id"] != os.getpid()
            for event in events
        ),
        "client data operations were not executed in subprocesses",
    )
    require(events[0].get("action") == "batch-search", "the first data action was not batch-search")
    require(sum(event.get("action") == "batch-search" for event in events) == 1, "searches were not issued as one batch")

    search_ops = events[0].get("operations")
    require(isinstance(search_ops, list) and len(search_ops) == 2, "search batch did not contain both branches")
    require(overlaps(search_ops), "the two searches did not execute concurrently")
    observed_queries = {
        (str(operation["input"]["name"]), str(operation["input"]["location"]))
        for operation in search_ops
    }
    require(observed_queries == set(TARGETS), "search batch did not use the two requested name/location pairs")

    eligible_ids: list[str] = []
    for operation in search_ops:
        result = operation.get("result")
        require(isinstance(result, list), "search operation did not return a stable-ID list")
        if len(result) == 1:
            eligible_ids.append(str(result[0]))

    get_events = [event for event in events if event.get("action") == "batch-get"]
    if eligible_ids:
        require(len(events) == 2 and len(get_events) == 1, "eligible gets were not the single next data action")
        get_ops = get_events[0].get("operations")
        require(isinstance(get_ops, list), "get batch lacks operation evidence")
        require({str(operation["input"]) for operation in get_ops} == set(eligible_ids), "get batch did not contain exactly the eligible stable IDs")
        require(len(get_ops) == len(eligible_ids), "get batch duplicated or omitted an eligible branch")
        require(overlaps(get_ops), "two eligible gets did not execute concurrently")
        records_by_id = {row["stable_id"]: row for row in rows}
        for operation in get_ops:
            require(operation.get("result") == records_by_id[str(operation["input"])], "get result was not the full stored record")
    else:
        require(len(events) == 1 and not get_events, "runner retrieved despite having no uniquely resolved branch")


def run_scenario(label: str, rows: list[dict[str, str]]) -> None:
    with tempfile.TemporaryDirectory(prefix=f"procurement-{label}-") as raw:
        root = Path(raw)
        runner = root / "audit_runner.py"
        client = root / "procurement_cli.py"
        data = root / "orders.csv"
        trace = root / "trace.jsonl"
        report = root / "audit_report.txt"
        guard = root / "sitecustomize.py"
        shutil.copy2(RUNNER, runner)
        shutil.copy2(CLIENT, client)
        write_rows(data, rows)
        guard.write_text(RUNNER_GUARD, encoding="utf-8")
        before = hashlib.sha256(data.read_bytes()).digest()
        env = os.environ.copy()
        env.update(
            PROCUREMENT_CLI=str(client),
            PROCUREMENT_DATA=str(data),
            PROCUREMENT_TRACE=str(trace),
            AUDIT_REPORT=str(report),
        )
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(root), env.get("PYTHONPATH")]))
        completed = subprocess.run(
            [sys.executable, str(runner)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        require(completed.returncode == 0, f"{label}: runner failed: {completed.stderr[-400:]}")
        require(hashlib.sha256(data.read_bytes()).digest() == before, f"{label}: record store was modified")
        require(report.is_file(), f"{label}: audit_report.txt was not written")
        lines = report.read_text(encoding="utf-8").splitlines()
        require(lines == expected_report(rows), f"{label}: report mismatch: {lines!r}")
        require(trace.is_file(), f"{label}: client did not write an execution trace")
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line]
        verify_trace(events, rows)


def main() -> int:
    require(RUNNER.is_file(), "audit_runner.py is missing")
    base = read_rows(DATA)
    run_scenario("primary", base)

    shifted = [dict(row) for row in base]
    for index, (name, location) in enumerate(TARGETS):
        target = next(row for row in shifted if row["name"] == name and row["location"] == location)
        target["stable_id"] = f"SHIFT-{index + 1}"
        target["order_date"] = f"2027-02-{12 - index:02d}"
        target["status"] = "Released" if index == 0 else "Quality Review"
    run_scenario("shifted-values", shifted)

    missing_first = [
        row for row in base if not (row["name"] == TARGETS[0][0] and row["location"] == TARGETS[0][1])
    ]
    run_scenario("missing-first", missing_first)

    missing_second = [
        row for row in base if not (row["name"] == TARGETS[1][0] and row["location"] == TARGETS[1][1])
    ]
    run_scenario("missing-second", missing_second)

    ambiguous_first = [dict(row) for row in base]
    duplicate = dict(next(row for row in base if row["name"] == TARGETS[0][0] and row["location"] == TARGETS[0][1]))
    duplicate.update(stable_id="ORD-AMB-1", order_date="2026-01-11", status="Manual Review")
    ambiguous_first.append(duplicate)
    run_scenario("ambiguous-first", ambiguous_first)

    ambiguous_second = [dict(row) for row in base]
    duplicate = dict(next(row for row in base if row["name"] == TARGETS[1][0] and row["location"] == TARGETS[1][1]))
    duplicate.update(stable_id="ORD-AMB-2", order_date="2026-01-18", status="Exception Review")
    ambiguous_second.append(duplicate)
    run_scenario("ambiguous-second", ambiguous_second)

    both_missing = [
        row for row in base if (row["name"], row["location"]) not in set(TARGETS)
    ]
    run_scenario("both-missing", both_missing)
    print("all procurement audit checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
