#!/usr/bin/env python3
"""Exercise the reference path through genuine crmctl processes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CRM_RUNTIME = ROOT / ".crm" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "24b2005b2f52c1926aedf9d52dd91a8ca204968aeff7bfd6481da04fb0a49bb1"


def reset_generated_state() -> None:
    for path in (
        CRM_RUNTIME / "crm.sqlite3",
        CRM_RUNTIME / "crm.sqlite3-shm",
        CRM_RUNTIME / "crm.sqlite3-wal",
        CRM_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "crm-audit.jsonl",
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


def run_help() -> None:
    subprocess.run(
        ["./crmctl", "--help"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


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
                f"reference CRM operation failed: {stderr.decode().strip()}"
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

    run_help()
    profile_payload = run(["./crmctl", "profile"])
    profile = profile_payload.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("default_date"), str):
        raise RuntimeError("reference profile did not return a default date")
    default_date = profile["default_date"]

    options = [
        ("Bluebird Literacy Project", "Northeast Region"),
        ("Mosaic Bicycle Works", "South Region"),
    ]
    results = concurrent_action(
        [
            [
                "./crmctl",
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
        if availability_value(result, f"{option[0]} in {option[1]}"):
            selected = option
            break
    if selected is None:
        return

    run(
        [
            "./crmctl",
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
