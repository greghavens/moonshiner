#!/usr/bin/env python3
"""Protected deterministic acceptance check for the messaging audit."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
GATEWAY = ROOT / "messaging"
DATABASE = ROOT / "messages.sqlite3"
REPORT = ROOT / "audit.md"

TARGETS = (
    ("Warehouse Cutoff Alert", "Fulfillment Team"),
    ("Carrier Delay Summary", "Customer Care"),
)
FORBIDDEN = {"list", "profile", "availability", "create", "update", "cancel", "notify"}


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def launch(commands: list[list[str]], env: dict[str, str]) -> list[dict]:
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for command in commands
    ]
    results: list[dict] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=5)
        if process.returncode != 0:
            raise AssertionError(
                f"gateway exited {process.returncode}: {stderr.strip() or stdout.strip()}"
            )
        try:
            results.append(json.loads(stdout))
        except json.JSONDecodeError as error:
            raise AssertionError(f"gateway emitted invalid JSON: {stdout!r}") from error
    return results


def intervals(records: list[dict], operation: str) -> list[tuple[int, int]]:
    by_pid: dict[int, dict[str, int]] = {}
    for record in records:
        if record["operation"] == operation:
            by_pid.setdefault(record["pid"], {})[record["event"]] = record["time_ns"]
    values = []
    for events in by_pid.values():
        if set(events) != {"start", "end"} or events["start"] >= events["end"]:
            raise AssertionError(f"incomplete {operation} execution trace")
        values.append((events["start"], events["end"]))
    return values


def overlap(pair: list[tuple[int, int]]) -> bool:
    return len(pair) == 2 and max(pair[0][0], pair[1][0]) < min(pair[0][1], pair[1][1])


def reference_execution(trace_path: Path) -> str:
    env = os.environ.copy()
    env["MESSAGING_TRACE"] = str(trace_path)
    env["MESSAGING_EXPECTED_PEERS"] = "2"

    searches = launch(
        [
            [str(GATEWAY), "search", "--name", name, "--location", location]
            for name, location in TARGETS
        ],
        env,
    )

    stable_ids: list[str | None] = []
    for result in searches:
        matches = result.get("matches")
        stable = [match for match in matches or [] if match.get("stable") is True]
        stable_ids.append(stable[0]["id"] if len(stable) == 1 else None)

    retrieved: list[dict | None] = [None, None]
    resolvable = [(index, identifier) for index, identifier in enumerate(stable_ids) if identifier]
    if resolvable:
        payloads = launch(
            [[str(GATEWAY), "get", "--id", identifier] for _, identifier in resolvable],
            env,
        )
        for (index, _), payload in zip(resolvable, payloads, strict=True):
            retrieved[index] = payload

    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    operations = [record["operation"] for record in records]
    if any(operation in FORBIDDEN for operation in operations):
        raise AssertionError("reference execution used a forbidden operation")
    search_intervals = intervals(records, "search")
    get_intervals = intervals(records, "get")
    if not overlap(search_intervals):
        raise AssertionError("the independent searches did not overlap")
    if len(get_intervals) == 2 and not overlap(get_intervals):
        raise AssertionError("the independent gets did not overlap")
    if get_intervals and min(start for start, _ in get_intervals) <= max(
        end for _, end in search_intervals
    ):
        raise AssertionError("a get began before all searches completed")

    statuses = [item.get("status") if item else None for item in retrieved]
    comparison = (
        "statuses match"
        if None not in statuses and statuses[0] == statuses[1]
        else "statuses differ"
        if None not in statuses
        else "unresolved"
    )
    first_status = statuses[0] or "unresolved"
    second_status = statuses[1] or "unresolved"
    return (
        f"- {TARGETS[0][0]} ({TARGETS[0][1]}): {first_status}\n"
        f"- {TARGETS[1][0]} ({TARGETS[1][1]}): {second_status}; comparison: {comparison}\n"
    )


def main() -> int:
    if not REPORT.is_file():
        return fail("audit.md is missing")
    before = digest(DATABASE)
    try:
        with sqlite3.connect(f"file:{DATABASE.as_posix()}?mode=ro", uri=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            return fail("the protected SQLite store is not intact")
        with tempfile.TemporaryDirectory(prefix="message-audit-") as directory:
            expected = reference_execution(Path(directory) / "trace.jsonl")
    except (AssertionError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        return fail(f"reference execution failed: {error}")
    if digest(DATABASE) != before:
        return fail("the read-only audit changed the SQLite store")
    try:
        actual = REPORT.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return fail("audit.md must be UTF-8 text")
    if actual != expected:
        return fail("audit.md does not match the grounded two-bullet audit")
    print("PASS: read-only dependency-planned messaging audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
