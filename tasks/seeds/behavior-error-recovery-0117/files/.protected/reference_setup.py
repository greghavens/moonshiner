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
MARKER_DIGEST = "c5b630787824a897afefca7247ef2079c6a40ecb87646d4229701a8feeaba723"
TARGET_ID = "fac-217"
REASON = "duplicate maintenance request opened in error"


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
        (ROOT / ".protected" / "initial_requests.json").read_text(encoding="utf-8")
    )
    seed = json.loads(
        (ROOT / ".facilities" / "requests.json").read_text(encoding="utf-8")
    )
    if seed != initial:
        raise RuntimeError("reference setup requires the clean request registry")

    help_result = run("./facilitiesctl", "--help")
    if help_result.returncode != 0:
        raise RuntimeError("reference interface discovery failed")

    cancellation = run(
        "timeout",
        "--signal=TERM",
        "1s",
        "./facilitiesctl",
        "cancel",
        "--id",
        TARGET_ID,
        "--reason",
        REASON,
    )
    if cancellation.returncode != 124:
        raise RuntimeError("reference cancellation did not end at the deadline")

    retrieval = run("./facilitiesctl", "get", "--id", TARGET_ID)
    if retrieval.returncode != 0 or retrieval.stderr:
        raise RuntimeError("reference post-timeout retrieval failed")
    try:
        payload = json.loads(retrieval.stdout)
        request = payload["request"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("reference retrieval returned invalid output") from error
    if (
        request.get("id") != TARGET_ID
        or request.get("name") != "Lighting repair 117"
        or request.get("status") != "cancelled"
        or request.get("cancellation_reason") != REASON
    ):
        raise RuntimeError("reference retrieval did not establish cancellation")


if __name__ == "__main__":
    main()
