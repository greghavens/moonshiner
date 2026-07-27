#!/usr/bin/env python3
"""Generate reference evidence through genuine tripctl executions."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TRIPCTL = "./bin/tripctl"
TARGETS = (
    ("Kyoto archives visit", "Kyoto"),
    ("Osaka supplier tour", "Osaka"),
)


def phase_worker() -> int:
    """Start both commands before collecting either response."""
    commands = json.loads(sys.stdin.read())
    if not isinstance(commands, list) or len(commands) != 2:
        raise RuntimeError("reference phase must contain exactly two commands")
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
    payloads: list[dict[str, Any]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=25)
        if process.returncode != 0:
            raise RuntimeError(stderr.strip() or "tripctl operation failed")
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("tripctl returned an invalid payload")
        payloads.append(payload)
    print(json.dumps(payloads))
    return 0


def run_phase(commands: list[list[str]]) -> list[dict[str, Any]]:
    """Use a fresh process for each dependency phase."""
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--phase-worker"],
        cwd=ROOT,
        input=json.dumps(commands),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=35,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "reference phase failed")
    payloads = json.loads(completed.stdout)
    if not isinstance(payloads, list) or not all(
        isinstance(payload, dict) for payload in payloads
    ):
        raise RuntimeError("reference phase returned invalid results")
    return payloads


def main() -> int:
    help_result = subprocess.run(
        [TRIPCTL, "--help"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=10,
    )
    if help_result.returncode != 0 or "search" not in help_result.stdout:
        raise RuntimeError(help_result.stderr.strip() or "tripctl help failed")

    searches = run_phase(
        [
            [TRIPCTL, "search", "--name", name, "--city", city]
            for name, city in TARGETS
        ]
    )

    trip_ids: list[str] = []
    for target, result in zip(TARGETS, searches, strict=True):
        identifiers = result.get("ids")
        if (
            result.get("count") != 1
            or not isinstance(identifiers, list)
            or len(identifiers) != 1
            or not isinstance(identifiers[0], str)
            or not identifiers[0]
        ):
            raise RuntimeError(f"reference search did not resolve uniquely: {target}")
        trip_ids.append(identifiers[0])

    retrievals = run_phase([[TRIPCTL, "get", "--id", trip_id] for trip_id in trip_ids])
    for result, target, trip_id in zip(
        retrievals, TARGETS, trip_ids, strict=True
    ):
        record = result.get("record")
        name, city = target
        if (
            result.get("found") is not True
            or not isinstance(record, dict)
            or record.get("id") != trip_id
            or record.get("name") != name
            or record.get("city") != city
        ):
            raise RuntimeError(f"reference retrieval returned a mismatch: {target}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--phase-worker"]:
        raise SystemExit(phase_worker())
    raise SystemExit(main())
