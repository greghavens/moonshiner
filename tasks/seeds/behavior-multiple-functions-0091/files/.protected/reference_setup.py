#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine registry executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".registry" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
MARKER_PATH = ROOT / ".reference_solution"
REPORT_PATH = ROOT / "candidate_record.json"
MARKER_DIGEST = "afc87c8334852ea36a3d989994cb2b5bf2a938962f8d30d2afca984e046b718e"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "candidates.sqlite3",
        REGISTRY_RUNTIME / "candidates.sqlite3-journal",
        REGISTRY_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "candidate-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_executable(arguments: list[str]) -> str:
    result = subprocess.run(
        ["./candidate-registry", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"reference registry operation failed: {result.stderr.strip()}"
        )
    return result.stdout


def main() -> None:
    if (
        not MARKER_PATH.is_file()
        or hashlib.sha256(MARKER_PATH.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    run_executable(["--help"])
    output = run_executable(
        ["search", "--name", "Morgan Lee", "--department", "Research"]
    )
    payload = json.loads(output)
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError("reference exact search was not uniquely resolved")
    match = matches[0]
    if (
        not isinstance(match, dict)
        or match.get("name") != "Morgan Lee"
        or match.get("department") != "Research"
    ):
        raise RuntimeError("reference exact search returned an out-of-scope record")
    if not REPORT_PATH.is_file() or REPORT_PATH.read_text(encoding="utf-8") != output:
        raise RuntimeError("reference patch is not grounded in registry output")


if __name__ == "__main__":
    main()
