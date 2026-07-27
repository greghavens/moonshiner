#!/usr/bin/env python3
"""Generate reference evidence through genuine beaconctl executions."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".beacon-runtime"
REQUESTED_NAME = "Policy Accessibility Review"
REQUESTED_LOCATION = "Beacon"
WORKER = r"""
import subprocess
import sys

completed = subprocess.run(
    sys.argv[1:],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
    timeout=15,
)
sys.stdout.buffer.write(completed.stdout)
sys.stderr.buffer.write(completed.stderr)
raise SystemExit(completed.returncode)
"""


def run_action(arguments: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-B", "-c", WORKER, *arguments],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.decode("utf-8", errors="replace").strip()
            or "catalog operation failed"
        )
    if completed.stderr:
        raise RuntimeError("catalog operation wrote unexpected stderr")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("catalog operation returned invalid JSON") from error
    if not isinstance(result, dict):
        raise RuntimeError("catalog operation returned a non-object")
    return result


def main() -> int:
    shutil.rmtree(RUNTIME, ignore_errors=True)

    search = run_action(["./beaconctl", "search", "--name", REQUESTED_NAME])
    matches = search.get("matches")
    if not isinstance(matches, list):
        raise RuntimeError("focused search did not return candidate summaries")
    qualifying = [
        match
        for match in matches
        if isinstance(match, dict)
        and match.get("name") == REQUESTED_NAME
        and match.get("location") == REQUESTED_LOCATION
    ]
    if len(qualifying) != 1:
        raise RuntimeError("focused search did not resolve one Beacon task")
    stable_id = qualifying[0].get("id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError("qualifying search summary returned no stable ID")

    detail = run_action(["./beaconctl", "get", "--id", stable_id])
    record = detail.get("record")
    if (
        not isinstance(record, dict)
        or record.get("id") != stable_id
        or record.get("name") != REQUESTED_NAME
        or record.get("location") != REQUESTED_LOCATION
    ):
        raise RuntimeError("dependent retrieval returned the wrong complete record")
    print("reference Beacon search and dependent retrieval executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
