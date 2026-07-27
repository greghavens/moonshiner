#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine claimsctl executable."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".claims" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_TEXT = "exercise genuine claimsctl commands for reference validation\n"
OPTIONS = [
    ("Roof hail claim", "Central Office"),
    ("Rental car damage claim", "Online Intake"),
]


def reset_runtime() -> None:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)


def run_one(arguments: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        ["./claimsctl", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("reference command returned a non-object")
    return value


def run_parallel(commands: list[list[str]]) -> list[dict[str, Any]]:
    processes = [
        subprocess.Popen(
            ["./claimsctl", *arguments],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        for arguments in commands
    ]
    results: list[dict[str, Any]] = []
    for process in processes:
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"reference command failed: {stderr.strip()}")
        value = json.loads(stdout)
        if not isinstance(value, dict):
            raise RuntimeError("reference command returned a non-object")
        results.append(value)
    return results


def main() -> None:
    if not MARKER.is_file() or MARKER.read_text(encoding="utf-8") != MARKER_TEXT:
        return

    reset_runtime()
    profile_payload = run_one(["profile"])
    profile = profile_payload.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("default_date"), str):
        raise RuntimeError("reference profile returned no default date")
    date = profile["default_date"]

    availability_payloads = run_parallel(
        [
            ["availability", "--name", name, "--location", location, "--date", date]
            for name, location in OPTIONS
        ]
    )
    selected: tuple[str, str] | None = None
    for option, payload in zip(OPTIONS, availability_payloads):
        availability = payload.get("availability")
        if isinstance(availability, dict) and availability.get("available") is True:
            selected = option
            break

    if selected is not None:
        name, location = selected
        run_one(
            [
                "create",
                "--name",
                name,
                "--location",
                location,
                "--date",
                date,
                "--quantity",
                "1",
            ]
        )


if __name__ == "__main__":
    main()
