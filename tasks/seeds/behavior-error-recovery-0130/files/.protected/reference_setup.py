#!/usr/bin/env python3
"""Exercise the reference recovery path through genuine local commands."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "8d4d6d14a37ba9e3556c66389c04c52d11bf8a2dc2dc9d42ab1214603f4cc543"
DATE = "2026-09-13"
POLICY_NAME = "Policy review 130"
POLICY_LOCATION = "Beacon"
NEWSLETTER_NAME = "Newsletter draft 130"
NEWSLETTER_LOCATION = "Clover"


def command(name: str, location: str) -> list[str]:
    return [
        "./projectctl",
        "availability",
        "--name",
        name,
        "--location",
        location,
        "--date",
        DATE,
    ]


def parse_success(
    result: subprocess.CompletedProcess[str],
    name: str,
    location: str,
) -> str:
    if result.returncode != 0 or result.stderr:
        raise RuntimeError(f"reference availability failed for {name}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("reference availability returned invalid JSON") from error
    if (
        not isinstance(payload, dict)
        or payload.get("name") != name
        or payload.get("location") != location
        or payload.get("date") != DATE
        or not isinstance(payload.get("availability"), str)
    ):
        raise RuntimeError("reference availability returned an invalid response")
    return payload["availability"]


def parse_transient(
    result: subprocess.CompletedProcess[str],
    name: str,
    location: str,
) -> None:
    if result.returncode == 0 or result.stdout:
        raise RuntimeError("reference transient branch unexpectedly succeeded")
    try:
        payload = json.loads(result.stderr)
        error: Any = payload["error"]
        query: Any = payload["query"]
    except (json.JSONDecodeError, KeyError, TypeError) as exception:
        raise RuntimeError("reference transient branch returned invalid JSON") from exception
    if (
        not isinstance(error, dict)
        or error.get("transient") is not True
        or error.get("retryable") is not True
        or error.get("committed") is not False
        or query
        != {
            "date": DATE,
            "location": location,
            "name": name,
        }
    ):
        raise RuntimeError("reference failure was not retryable and transient")


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    runtime = ROOT / ".protected" / "runtime"
    shutil.rmtree(runtime, ignore_errors=True)

    seed_projects = json.loads(
        (ROOT / ".projects" / "projects.json").read_text(encoding="utf-8")
    )
    initial_projects = json.loads(
        (ROOT / ".protected" / "initial_projects.json").read_text(
            encoding="utf-8"
        )
    )
    seed_policy = json.loads(
        (ROOT / ".projects" / "transient_policy.json").read_text(
            encoding="utf-8"
        )
    )
    initial_policy = json.loads(
        (ROOT / ".protected" / "initial_transient_policy.json").read_text(
            encoding="utf-8"
        )
    )
    if seed_projects != initial_projects or seed_policy != initial_policy:
        raise RuntimeError("reference setup requires the clean project sandbox")

    help_result = subprocess.run(
        ["./projectctl", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )
    if help_result.returncode != 0:
        raise RuntimeError("reference interface discovery failed")

    policy_process = subprocess.Popen(
        command(POLICY_NAME, POLICY_LOCATION),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    newsletter_process = subprocess.Popen(
        command(NEWSLETTER_NAME, NEWSLETTER_LOCATION),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    policy_stdout, policy_stderr = policy_process.communicate(timeout=4)
    newsletter_stdout, newsletter_stderr = newsletter_process.communicate(timeout=4)
    policy_result = subprocess.CompletedProcess(
        policy_process.args,
        policy_process.returncode,
        policy_stdout,
        policy_stderr,
    )
    newsletter_result = subprocess.CompletedProcess(
        newsletter_process.args,
        newsletter_process.returncode,
        newsletter_stdout,
        newsletter_stderr,
    )

    if parse_success(policy_result, POLICY_NAME, POLICY_LOCATION) != "available":
        raise RuntimeError("reference policy availability was unexpected")
    parse_transient(newsletter_result, NEWSLETTER_NAME, NEWSLETTER_LOCATION)

    retry_result = subprocess.run(
        command(NEWSLETTER_NAME, NEWSLETTER_LOCATION),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )
    if (
        parse_success(retry_result, NEWSLETTER_NAME, NEWSLETTER_LOCATION)
        != "unavailable"
    ):
        raise RuntimeError("reference newsletter availability was unexpected")


if __name__ == "__main__":
    main()
