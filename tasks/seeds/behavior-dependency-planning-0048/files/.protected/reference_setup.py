#!/usr/bin/env python3
"""Generate reference evidence by executing the genuine shipment CLI."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUESTED = [
    ("Northstar Equipment Pallet", "Minneapolis Hub"),
    ("Lakeside Book Carton", "Madison Hub"),
]


def run_parallel(commands: list[list[str]]) -> list[dict]:
    if len(commands) != 2:
        raise AssertionError("the reference stage must have exactly two branches")
    shell = "\n".join(
        [
            "set -eu",
            f"{shlex.join(commands[0])} &",
            "first=$!",
            f"{shlex.join(commands[1])} &",
            "second=$!",
            'wait "$first"',
            'wait "$second"',
        ]
    )
    completed = subprocess.run(
        ["bash", "-c", shell],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    responses = [
        json.loads(line) for line in completed.stdout.splitlines() if line.strip()
    ]
    if len(responses) != 2:
        raise RuntimeError(f"parallel stage returned {len(responses)} responses")
    return responses


def resolved_id(response: dict, pair: tuple[str, str]) -> str:
    matches = response.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference branch is not unique: {pair[0]} at {pair[1]}")
    match = matches[0]
    if match.get("name") != pair[0] or match.get("location") != pair[1]:
        raise RuntimeError("search returned the wrong branch")
    stable_id = match.get("id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError("search returned no stable ID")
    return stable_id


def main() -> int:
    shutil.rmtree(ROOT / ".pi-runtime", ignore_errors=True)
    searches = run_parallel(
        [
            ["./bin/shipmentctl", "search", "--name", name, "--location", location]
            for name, location in REQUESTED
        ]
    )
    by_pair = {}
    for response in searches:
        for match in response.get("matches", []):
            by_pair[(match.get("name"), match.get("location"))] = response
    identifiers = [resolved_id(by_pair[pair], pair) for pair in REQUESTED]
    records = run_parallel(
        [["./bin/shipmentctl", "get", "--id", stable_id] for stable_id in identifiers]
    )
    if {record.get("id") for record in records} != set(identifiers):
        raise RuntimeError("full-record retrieval did not return both searched IDs")
    print("reference shipment workflow executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
