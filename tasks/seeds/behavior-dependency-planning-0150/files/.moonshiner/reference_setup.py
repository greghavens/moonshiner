#!/usr/bin/env python3
"""Exercise the reference path through genuine opsdesk processes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".opsdesk"
RUNTIME = DATA / "runtime"
CLIENT = "bin/opsdesk"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "12b542b6a00523b2c2bd82afdfa4d19984e3926ce93a05cb68dc6f15859a34c2"


def reset_generated_state() -> None:
    shutil.rmtree(RUNTIME, ignore_errors=True)


def run(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"reference operation returned non-object JSON: {command}")
    return payload


def run_help() -> None:
    subprocess.run(
        [CLIENT, "--help"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def concurrent_action(commands: list[list[str]]) -> list[dict[str, Any]]:
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        for command in commands
    ]
    results: list[dict[str, Any]] = []
    for process in processes:
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"reference availability operation failed: {stderr.strip()}")
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("reference availability operation returned non-object JSON")
        results.append(payload)
    return results


def main() -> None:
    reset_generated_state()
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    run_help()
    profile = run([CLIENT, "profile"])
    default_date = profile.get("default_date")
    if not isinstance(default_date, str) or not default_date:
        raise RuntimeError("reference profile did not return one default date")

    options = [
        ("Harbor Initiative", "Oral history catalog"),
        ("Juniper Team", "Community garden map"),
    ]
    checks = concurrent_action(
        [
            [
                CLIENT,
                "availability",
                "--team",
                team,
                "--option",
                option,
                "--date",
                default_date,
            ]
            for team, option in options
        ]
    )

    selected: dict[str, Any] | None = None
    for (team, option), check in zip(options, checks):
        if (
            check.get("team") != team
            or check.get("option") != option
            or check.get("date") != default_date
            or not isinstance(check.get("available"), bool)
            or not isinstance(check.get("option_id"), str)
            or not check["option_id"]
        ):
            raise RuntimeError(f"reference availability result was invalid: {team}/{option}")
        if selected is None and check["available"]:
            selected = check

    if selected is None:
        return

    run(
        [
            CLIENT,
            "create",
            "--option-id",
            selected["option_id"],
            "--date",
            default_date,
            "--quantity",
            "1",
        ]
    )


if __name__ == "__main__":
    main()
