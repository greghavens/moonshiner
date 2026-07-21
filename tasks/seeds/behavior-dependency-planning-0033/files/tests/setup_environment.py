#!/usr/bin/env python3
"""Initialize runtime state; exercise the real client for the reference patch."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".branch_catalog"
DATABASE = RUNTIME / "catalog.sqlite"
CLIENT = ROOT / "bin" / "branch_catalog"
REPORT = ROOT / "handoff-audit.md"


def direct_parallel(commands: list[list[str]]) -> list[dict]:
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for command in commands
    ]
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        if process.returncode != 0:
            raise RuntimeError(f"reference client failed: {stderr.strip()}")
        results.append(json.loads(stdout))
    return results


def parallel(commands: list[list[str]]) -> list[dict]:
    """Give each logical stage its own coordinator process/action boundary."""
    process = subprocess.run(
        [sys.executable, "-B", str(Path(__file__)), "--parallel-worker"],
        cwd=ROOT,
        input=json.dumps(commands),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if process.returncode != 0:
        raise RuntimeError(f"reference stage failed: {process.stderr.strip()}")
    return json.loads(process.stdout)


def main() -> int:
    if sys.argv[1:] == ["--parallel-worker"]:
        commands = json.loads(sys.stdin.read())
        if not isinstance(commands, list):
            raise RuntimeError("parallel worker expected a command list")
        print(json.dumps(direct_parallel(commands)))
        return 0

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir()
    connection = sqlite3.connect(DATABASE)
    try:
        connection.executescript((ROOT / "data" / "catalog.sql").read_text())
        connection.commit()
    finally:
        connection.close()

    # Trace setup stops here. When validate_seeds applies the reference patch,
    # the report's presence asks setup to prove it through genuine executions.
    if not REPORT.is_file():
        return 0

    searches = parallel(
        [
            [
                str(CLIENT),
                "search",
                "--name",
                "Orchard Weather Journal",
                "--location",
                "North Branch",
            ],
            [
                str(CLIENT),
                "search",
                "--name",
                "A Field Guide to Civic Murals",
                "--location",
                "Downtown Branch",
            ],
        ]
    )
    stable_ids = []
    for search in searches:
        matches = search.get("matches")
        if not isinstance(matches, list) or len(matches) != 1:
            raise RuntimeError("reference search did not resolve exactly one stable ID")
        stable_ids.append(matches[0]["stable_id"])
    parallel(
        [
            [str(CLIENT), "get", "--stable-id", stable_ids[0]],
            [str(CLIENT), "get", "--stable-id", stable_ids[1]],
        ]
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"setup_environment: {error}", file=sys.stderr)
        raise SystemExit(1)
