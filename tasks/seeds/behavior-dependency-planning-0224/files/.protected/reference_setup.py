#!/usr/bin/env python3
"""Run the genuine registry executable for post-patch reference validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "campaign-registry"
REPORT = ROOT / "campaign_comparison.md"
REPORT_SHA256 = "385108df55a4a6774a66c12f2d9b18da0a8fdaa380d4c4c2489246ed05191260"
CAMPAIGN_RUNTIME = ROOT / ".campaign" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
SCOPES = (
    ("Museum donor thank-you", "Donors"),
    ("Fall enrollment notice", "Students"),
)


def run_process(command: list[str]) -> str:
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    stdout, stderr = process.communicate(timeout=20)
    if process.returncode != 0:
        raise RuntimeError(stderr.strip() or f"command failed: {process.returncode}")
    return stdout


def run_pair(commands: list[list[str]]) -> list[dict[str, Any]]:
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        for command in commands
    ]
    results: list[dict[str, Any]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        if process.returncode != 0:
            raise RuntimeError(
                stderr.strip() or f"parallel command failed: {process.returncode}"
            )
        result = json.loads(stdout)
        if not isinstance(result, dict):
            raise RuntimeError("registry output is not an object")
        results.append(result)
    return results


def reset_runtime() -> None:
    for directory in (CAMPAIGN_RUNTIME, PROTECTED_RUNTIME):
        if directory.exists():
            shutil.rmtree(directory)


def main() -> int:
    if not REPORT.is_file() or hashlib.sha256(REPORT.read_bytes()).hexdigest() != REPORT_SHA256:
        raise RuntimeError("the reference patch was not applied intact")

    reset_runtime()
    run_process([str(TOOL), "--help"])
    searches = run_pair(
        [
            [
                str(TOOL),
                "search",
                "--name",
                name,
                "--location",
                location,
            ]
            for name, location in SCOPES
        ]
    )
    stable_ids: list[str] = []
    for result in searches:
        matches = result.get("matches")
        if not isinstance(matches, list) or len(matches) != 1:
            raise RuntimeError("a reference search did not resolve uniquely")
        stable_id = matches[0].get("id") if isinstance(matches[0], dict) else None
        if not isinstance(stable_id, str) or not stable_id:
            raise RuntimeError("a reference search did not return a stable ID")
        stable_ids.append(stable_id)

    retrieved = run_pair(
        [[str(TOOL), "get", "--id", stable_id] for stable_id in stable_ids]
    )
    if any(not isinstance(result.get("record"), dict) for result in retrieved):
        raise RuntimeError("a reference get did not return a complete record object")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as error:
        print(f"reference setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
