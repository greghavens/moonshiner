#!/usr/bin/env python3
"""Exercise the reference path through the genuine availability executable."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "d50e64a94d3ce6d17dac7bbcab07d23c9ded2372f05109ba52e4981c741c4a2a"


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    shutil.rmtree(RUNTIME, ignore_errors=True)
    help_result = subprocess.run(
        ["./claim-availability", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
        start_new_session=True,
    )
    if help_result.returncode != 0 or not help_result.stdout.strip():
        raise RuntimeError(
            f"reference help failed with {help_result.returncode}: "
            f"{help_result.stderr.strip()}"
        )
    commands = [
        [
            "./claim-availability",
            "check",
            "--date",
            "2026-11-25",
            "--item",
            "Theft claim",
            "--office",
            "West Office",
        ],
        [
            "./claim-availability",
            "check",
            "--date",
            "2026-11-25",
            "--item",
            "Windshield claim",
            "--office",
            "North Office",
        ],
    ]
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        for command in commands
    ]
    results = [process.communicate(timeout=5) for process in processes]
    codes = [process.returncode for process in processes]
    if codes != [0, 75]:
        raise RuntimeError(f"reference first action returned unexpected codes: {codes}")
    if not results[0][0].strip() or not results[1][1].strip():
        raise RuntimeError("reference first action did not produce both branch results")

    retry = subprocess.run(
        commands[1],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
        start_new_session=True,
    )
    if retry.returncode != 0 or not retry.stdout.strip():
        raise RuntimeError(
            f"reference retry failed with {retry.returncode}: {retry.stderr.strip()}"
        )


if __name__ == "__main__":
    main()
