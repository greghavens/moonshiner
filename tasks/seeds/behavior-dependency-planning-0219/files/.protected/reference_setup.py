#!/usr/bin/env python3
"""Generate reference audit evidence through genuine permit-registry executions."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "./permit-registry"
TARGETS = (
    ("Fort Collins", "Garden water rebate — Elm Street"),
    ("Pueblo", "Food cart permit — Rosa's Kitchen"),
)


def phase_worker() -> int:
    """Start all commands for one action before collecting either response."""
    commands = json.loads(sys.stdin.read())
    if not isinstance(commands, list) or not commands:
        raise RuntimeError("reference phase has no commands")
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
            raise RuntimeError(stderr.strip() or "permit-registry operation failed")
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("permit-registry returned an invalid payload")
        payloads.append(payload)
    print(json.dumps(payloads, ensure_ascii=False))
    return 0


def run_phase(commands: list[list[str]]) -> list[dict[str, Any]]:
    """Use a fresh action parent for each dependency phase."""
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--phase-worker"],
        cwd=ROOT,
        input=json.dumps(commands, ensure_ascii=False),
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
                "--city",
                city,
                "--application",
                application,
                "--exact",
            ]
            for city, application in TARGETS
        ]
    )

    application_ids: list[str] = []
    for target, result in zip(TARGETS, searches, strict=True):
        matches = result.get("matches")
        if result.get("count") != 1 or not isinstance(matches, list) or len(matches) != 1:
            raise RuntimeError(f"reference search did not resolve uniquely: {target}")
        match = matches[0]
        if not isinstance(match, dict):
            raise RuntimeError(f"reference search returned an invalid match: {target}")
        city, application = target
        application_id = match.get("application_id")
        if (
            match.get("city") != city
            or match.get("application") != application
            or not isinstance(application_id, str)
            or not application_id
        ):
            raise RuntimeError(f"reference search returned a mismatched record: {target}")
        application_ids.append(application_id)

    retrieval = run_phase([[REGISTRY, "get", *application_ids]])[0]
    records = retrieval.get("applications")
    if not isinstance(records, list) or len(records) != 2:
        raise RuntimeError("reference retrieval did not return both complete records")
    for record, target, application_id in zip(
        records, TARGETS, application_ids, strict=True
    ):
        city, application = target
        if (
            not isinstance(record, dict)
            or record.get("application_id") != application_id
            or record.get("city") != city
            or record.get("application") != application
            or not isinstance(record.get("status"), str)
            or not isinstance(record.get("date"), str)
        ):
            raise RuntimeError(f"reference retrieval returned a mismatched record: {target}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--phase-worker"]:
        raise SystemExit(phase_worker())
    raise SystemExit(main())
