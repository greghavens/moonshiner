#!/usr/bin/env python3
"""Generate reference evidence through genuine fleet-registry executions."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "./fleet-registry"
TARGETS = (
    ("Depot B", "Truck 8 garden delivery"),
    ("Depot C", "Sedan 4 clinic courier"),
)


def phase_worker() -> int:
    """Start both commands for one action before collecting either response."""
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
        stdout, stderr = process.communicate(timeout=15)
        if process.returncode != 0:
            raise RuntimeError(stderr.strip() or "fleet-registry operation failed")
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("fleet-registry returned an invalid payload")
        payloads.append(payload)
    print(json.dumps(payloads))
    return 0


def run_phase(commands: list[list[str]]) -> list[dict[str, Any]]:
    """Use a fresh action parent for each dependency phase."""
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--phase-worker"],
        cwd=ROOT,
        input=json.dumps(commands),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
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
    searches = run_phase(
        [
            [
                REGISTRY,
                "search",
                "--location",
                location,
                "--name",
                name,
                "--exact",
            ]
            for location, name in TARGETS
        ]
    )

    vehicle_ids: list[str] = []
    for target, result in zip(TARGETS, searches, strict=True):
        matches = result.get("matches")
        if result.get("count") != 1 or not isinstance(matches, list) or len(matches) != 1:
            raise RuntimeError(f"reference search did not resolve uniquely: {target}")
        match = matches[0]
        if not isinstance(match, dict):
            raise RuntimeError(f"reference search returned an invalid match: {target}")
        location, name = target
        vehicle_id = match.get("vehicle_id")
        if (
            match.get("location") != location
            or match.get("name") != name
            or not isinstance(vehicle_id, str)
            or not vehicle_id
        ):
            raise RuntimeError(f"reference search returned a mismatched record: {target}")
        vehicle_ids.append(vehicle_id)

    retrievals = run_phase([[REGISTRY, "get", vehicle_id] for vehicle_id in vehicle_ids])
    for result, target, vehicle_id in zip(
        retrievals, TARGETS, vehicle_ids, strict=True
    ):
        record = result.get("vehicle")
        location, name = target
        if (
            not isinstance(record, dict)
            or record.get("vehicle_id") != vehicle_id
            or record.get("location") != location
            or record.get("name") != name
            or not isinstance(record.get("status"), str)
            or not isinstance(record.get("date"), str)
        ):
            raise RuntimeError(f"reference retrieval returned a mismatched record: {target}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--phase-worker"]:
        raise SystemExit(phase_worker())
    raise SystemExit(main())
