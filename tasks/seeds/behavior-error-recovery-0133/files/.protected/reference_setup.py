#!/usr/bin/env python3
"""Exercise the reference recovery path through genuine local commands."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "970366d1af338aefbedf34a4beee9f6c2d8b2392bba9946fff6cc7bf02854517"
TARGET_ID = "lib-233"
REASON = "superseded edition removed from circulation."


def run(*arguments: str, timeout: int = 4) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    runtime = ROOT / ".protected" / "runtime"
    shutil.rmtree(runtime, ignore_errors=True)

    initial = json.loads(
        (ROOT / ".protected" / "initial_records.json").read_text(encoding="utf-8")
    )
    seed = json.loads(
        (ROOT / ".catalog" / "records.json").read_text(encoding="utf-8")
    )
    if seed != initial:
        raise RuntimeError("reference setup requires the clean catalog")

    help_result = run("./catalog", "--help")
    if help_result.returncode != 0:
        raise RuntimeError("reference interface discovery failed")

    cancellation = run(
        "timeout",
        "--signal=TERM",
        "1s",
        "./catalog",
        "cancel",
        "--id",
        TARGET_ID,
        "--reason",
        REASON,
    )
    if cancellation.returncode != 124:
        raise RuntimeError("reference cancellation did not end at the deadline")

    retrieval = run("./catalog", "get", "--id", TARGET_ID)
    if retrieval.returncode != 0 or retrieval.stderr:
        raise RuntimeError("reference post-timeout retrieval failed")
    try:
        payload = json.loads(retrieval.stdout)
        record = payload["record"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("reference retrieval returned invalid output") from error
    if (
        record.get("id") != TARGET_ID
        or record.get("title") != "River Almanac 133"
        or record.get("status") != "cancelled"
        or record.get("cancellation_reason") != REASON
    ):
        raise RuntimeError("reference retrieval did not establish cancellation")


if __name__ == "__main__":
    main()
