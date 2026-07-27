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
MARKER_DIGEST = "532a3876582000e07fa2fbd3adb6fd699ea8def93c10e178ac9515902bfe4dd3"
TARGET_ID = "inv-227"
REASON = "duplicate stock record approved for retirement"


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
        (ROOT / ".protected" / "initial_items.json").read_text(encoding="utf-8")
    )
    seed = json.loads(
        (ROOT / ".inventory" / "items.json").read_text(encoding="utf-8")
    )
    if seed != initial:
        raise RuntimeError("reference setup requires the clean inventory registry")

    help_result = run("./inventoryctl", "--help")
    if help_result.returncode != 0:
        raise RuntimeError("reference interface discovery failed")

    cancellation = run(
        "timeout",
        "--signal=TERM",
        "1s",
        "./inventoryctl",
        "cancel",
        "--id",
        TARGET_ID,
        "--reason",
        REASON,
    )
    if cancellation.returncode != 124:
        raise RuntimeError("reference cancellation did not end at the deadline")

    retrieval = run("./inventoryctl", "get", "--id", TARGET_ID)
    if retrieval.returncode != 0 or retrieval.stderr:
        raise RuntimeError("reference post-timeout retrieval failed")
    try:
        payload = json.loads(retrieval.stdout)
        item = payload["item"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("reference retrieval returned invalid output") from error
    if (
        item.get("id") != TARGET_ID
        or item.get("name") != "Packing tape 127"
        or item.get("status") != "cancelled"
        or item.get("cancellation_reason") != REASON
    ):
        raise RuntimeError("reference retrieval did not establish cancellation")


if __name__ == "__main__":
    main()
