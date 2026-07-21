#!/usr/bin/env python3
"""Generate reference runtime evidence by executing the real case CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_parallel(commands: list[list[str]]) -> list[dict]:
    if len(commands) != 2:
        raise AssertionError("the reference workflow has exactly two branches")
    shell = "\n".join(
        [
            "set -eu",
            f"{' '.join(quote(part) for part in commands[0])} &",
            "first=$!",
            f"{' '.join(quote(part) for part in commands[1])} &",
            "second=$!",
            "wait \"$first\"",
            "wait \"$second\"",
        ]
    )
    completed = subprocess.run(
        ["bash", "-c", shell], cwd=ROOT, check=True,
        capture_output=True, text=True,
    )
    records = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    if len(records) != 2:
        raise RuntimeError(f"parallel stage returned {len(records)} records")
    return records


def quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def unique_id(search_response: dict, name: str, location: str) -> str:
    matches = search_response.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference branch is not unique: {name} at {location}")
    match = matches[0]
    if match.get("name") != name or match.get("location") != location:
        raise RuntimeError("search returned the wrong branch")
    stable_id = match.get("id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError("search returned no stable ID")
    return stable_id


def main() -> int:
    shutil.rmtree(ROOT / ".pytest_cache", ignore_errors=True)
    requested = [
        ("Accessibility Caption Delay", "Elm Learning"),
        ("Incorrect Renewal Date", "Frost Dental"),
    ]
    searches = run_parallel(
        [
            ["./bin/casectl", "search", "--name", name, "--location", location]
            for name, location in requested
        ]
    )
    by_pair = {
        (match["name"], match["location"]): response
        for response in searches
        for match in response.get("matches", [])
    }
    identifiers = [
        unique_id(by_pair[(name, location)], name, location)
        for name, location in requested
    ]
    gets = run_parallel(
        [["./bin/casectl", "get", "--id", stable_id] for stable_id in identifiers]
    )
    if {record.get("id") for record in gets} != set(identifiers):
        raise RuntimeError("full-record retrieval did not return both searched IDs")
    print("reference case workflow executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
