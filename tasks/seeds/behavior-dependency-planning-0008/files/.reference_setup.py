#!/usr/bin/env python3
"""Replay a reference-only plan through the real sandbox executable."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
PLAN = ROOT / ".reference-plan.json"
TOOL = ROOT / "shipdesk"


def invoke(arguments: list[str]) -> dict:
    result = subprocess.run(
        [str(TOOL), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "shipdesk invocation failed")
    return json.loads(result.stdout)


def invoke_help() -> None:
    result = subprocess.run(
        [str(TOOL), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "shipdesk help invocation failed")
    if "search" not in result.stdout or "get" not in result.stdout:
        raise RuntimeError("shipdesk help did not describe its read operations")


def main() -> int:
    if not PLAN.exists():
        print("reference plan absent; no setup needed")
        return 0
    plan = json.loads(PLAN.read_text())
    searches = plan.get("searches")
    if not isinstance(searches, list) or len(searches) != 2:
        raise ValueError("reference plan must contain two searches")

    invoke_help()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda item: invoke([
                "search",
                "--name", str(item["name"]),
                "--location", str(item["location"]),
            ]),
            searches,
        ))

    ids: list[str] = []
    for result in results:
        matches = result.get("matches")
        if not isinstance(matches, list) or len(matches) != 1:
            raise RuntimeError("reference search did not resolve uniquely")
        record_id = matches[0].get("id")
        if not isinstance(record_id, str) or not record_id:
            raise RuntimeError("reference search did not return a stable ID")
        ids.append(record_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda record_id: invoke(["get", "--id", record_id]), ids))
    if len(records) != 2:
        raise RuntimeError("reference retrieval did not complete")
    print("reference executable flow completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
