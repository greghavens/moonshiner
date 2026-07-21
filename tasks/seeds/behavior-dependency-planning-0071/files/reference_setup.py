#!/usr/bin/env python3
"""Exercise the genuine executable when validating the shipped reference patch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "48d75eddcd6bab7ca6b9bd52452c0744c45aa5ab5f63743de44242e4c647fd15"
RUNTIME = ROOT / ".candidate_audit"
TARGETS = [
    ("Noah Williams - Support Lead", "Customer Care"),
    ("Leila Haddad - Grants Manager", "Programs"),
]


def run_pair(commands: list[list[str]]) -> list[dict]:
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
    outputs: list[dict] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        if process.returncode != 0:
            raise RuntimeError(stderr.strip() or f"command failed with {process.returncode}")
        outputs.append(json.loads(stdout))
    return outputs


def main() -> int:
    if not MARKER.is_file() or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST:
        return 0
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)

    searches = run_pair([
        [str(ROOT / "candidate_records"), "search", "--name", name, "--location", location]
        for name, location in TARGETS
    ])
    ids: list[str] = []
    for result in searches:
        matches = result.get("matches", [])
        if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
            raise RuntimeError("reference branch did not resolve uniquely")
        ids.append(matches[0]["id"])
    run_pair([
        [str(ROOT / "candidate_records"), "get", "--id", record_id]
        for record_id in ids
    ])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"reference setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
