#!/usr/bin/env python3
"""Produce genuine reference runtime evidence without encoding stable IDs."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "bin" / "message-audit"
DATABASE = ROOT / "data" / "messages.sqlite3"
EVIDENCE = ROOT / ".audit-runtime"


def targets() -> list[tuple[str, str]]:
    uri = f"file:{DATABASE}?mode=ro"
    with sqlite3.connect(uri, uri=True) as database:
        return database.execute(
            "SELECT name, location FROM audit_targets ORDER BY position"
        ).fetchall()


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
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        if process.returncode != 0:
            raise RuntimeError(stderr.strip() or "messaging command failed")
        results.append(json.loads(stdout))
    return results


def main() -> int:
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)

    search_results = run_pair([
        [str(TOOL), "search", "--name", name, "--location", location]
        for name, location in targets()
    ])
    stable_ids = []
    for result in search_results:
        matches = result.get("matches", [])
        if len(matches) != 1 or not matches[0].get("id"):
            raise RuntimeError("reference target did not resolve uniquely")
        stable_ids.append(matches[0]["id"])

    run_pair([
        [str(TOOL), "get", "--id", stable_id]
        for stable_id in stable_ids
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
