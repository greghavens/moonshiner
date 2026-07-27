#!/usr/bin/env python3
"""Generate reference evidence through genuine reservation-registry executions."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "./reservation-registry"
TARGETS = (
    ("Juniper Hall reception", "Boise"),
    ("Orchid Suite retreat", "Savannah"),
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
            raise RuntimeError(stderr.strip() or "reservation-registry operation failed")
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("reservation-registry returned an invalid payload")
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
        timeout=25,
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
        [REGISTRY, "--help"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=10,
    )
    if help_result.returncode != 0 or "search" not in help_result.stdout:
        raise RuntimeError(help_result.stderr.strip() or "reservation-registry help failed")

    searches = run_phase(
        [
            [REGISTRY, "search", "--name", name, "--city", city]
            for name, city in TARGETS
        ]
    )

    stable_ids: list[str] = []
    for target, result in zip(TARGETS, searches, strict=True):
        matches = result.get("matches")
        if result.get("count") != 1 or not isinstance(matches, list) or len(matches) != 1:
            raise RuntimeError(f"reference search did not resolve uniquely: {target}")
        match = matches[0]
        if not isinstance(match, dict):
            raise RuntimeError(f"reference search returned an invalid match: {target}")
        name, city = target
        stable_id = match.get("id")
        if (
            match.get("name") != name
            or match.get("city") != city
            or not isinstance(stable_id, str)
            or not stable_id
        ):
            raise RuntimeError(f"reference search returned a mismatch: {target}")
        stable_ids.append(stable_id)

    retrievals = run_phase(
        [[REGISTRY, "get", "--id", stable_id] for stable_id in stable_ids]
    )
    for result, target, stable_id in zip(
        retrievals, TARGETS, stable_ids, strict=True
    ):
        record = result.get("record")
        name, city = target
        if (
            not isinstance(record, dict)
            or record.get("id") != stable_id
            or record.get("name") != name
            or record.get("city") != city
            or not isinstance(record.get("status"), str)
            or not isinstance(record.get("date"), str)
        ):
            raise RuntimeError(f"reference retrieval returned a mismatch: {target}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--phase-worker"]:
        raise SystemExit(phase_worker())
    raise SystemExit(main())
