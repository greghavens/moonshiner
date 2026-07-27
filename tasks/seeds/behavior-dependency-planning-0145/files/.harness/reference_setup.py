#!/usr/bin/env python3
"""Exercise the reference path through genuine support-registry processes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SUPPORT_RUNTIME = ROOT / ".support" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "41f61d8289960f73ba6d1e2c04a53c3986931129b9be1ad65e73928d66ffdfca"


def reset_generated_state() -> None:
    for path in (
        SUPPORT_RUNTIME / "support.sqlite3",
        SUPPORT_RUNTIME / "support.sqlite3-shm",
        SUPPORT_RUNTIME / "support.sqlite3-wal",
        SUPPORT_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "support-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run(command: list[str]) -> dict:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    return json.loads(result.stdout)


def concurrent_action(commands: list[list[str]]) -> list[dict]:
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        for command in commands
    ]
    results: list[dict] = []
    for process in processes:
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"reference registry operation failed: {stderr.decode().strip()}"
            )
        results.append(json.loads(stdout))
    return results


def availability_value(payload: dict, label: str) -> bool:
    result = payload.get("availability")
    if not isinstance(result, dict) or not isinstance(result.get("available"), bool):
        raise RuntimeError(f"reference availability response is invalid: {label}")
    return result["available"]


def main() -> None:
    reset_generated_state()
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    profile_payload = run(["./support-registry", "profile"])
    profile = profile_payload.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("default_date"), str):
        raise RuntimeError("reference profile did not return a default date")
    default_date = profile["default_date"]

    options = [
        ("Captioning request case", "Cedar Clinic"),
        ("Mobile app login case", "Delta Library"),
    ]
    results = concurrent_action(
        [
            [
                "./support-registry",
                "availability",
                "--name",
                name,
                "--location",
                location,
                "--date",
                default_date,
            ]
            for name, location in options
        ]
    )

    selected: tuple[str, str] | None = None
    for option, result in zip(options, results):
        if availability_value(result, f"{option[0]} at {option[1]}"):
            selected = option
            break
    if selected is None:
        return

    run(
        [
            "./support-registry",
            "create",
            "--name",
            selected[0],
            "--location",
            selected[1],
            "--date",
            default_date,
            "--quantity",
            "1",
        ]
    )


if __name__ == "__main__":
    main()
