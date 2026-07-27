#!/usr/bin/env python3
"""Exercise the reference path through genuine orderctl processes."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".order-runtime"
CLIENT = ROOT / "orderctl"
REPORT = ROOT / "availability-report.md"
TARGETS = (
    ("Office order 126", "Boise", "2026-09-27"),
    ("Gift order 126", "Phoenix", "2026-09-27"),
)


def command_for(target: tuple[str, str, str]) -> list[str]:
    order, location, availability_date = target
    return [
        str(CLIENT),
        "availability",
        "--order",
        order,
        "--location",
        location,
        "--date",
        availability_date,
    ]


def start(target: tuple[str, str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command_for(target),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def collect(
    process: subprocess.Popen[str],
    target: tuple[str, str, str],
) -> tuple[int, dict[str, Any]]:
    stdout, stderr = process.communicate(timeout=15)
    raw = stdout if process.returncode == 0 else stderr
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid executable response for {target[0]}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"non-object executable response for {target[0]}")
    return int(process.returncode), payload


def successful_result(payload: dict[str, Any]) -> bool:
    return (
        isinstance(payload.get("available"), bool)
        and isinstance(payload.get("order"), str)
        and isinstance(payload.get("location"), str)
        and isinstance(payload.get("date"), str)
    )


def exercise_reference() -> str:
    help_result = subprocess.run(
        [str(CLIENT), "--help"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    if help_result.returncode != 0:
        raise RuntimeError("orderctl built-in help failed")

    processes = [(target, start(target)) for target in TARGETS]
    initial = [
        (target, *collect(process, target))
        for target, process in processes
    ]
    results: dict[tuple[str, str, str], dict[str, Any]] = {}
    failed: tuple[str, str, str] | None = None
    for target, returncode, payload in initial:
        if returncode == 0 and successful_result(payload):
            results[target] = payload
        elif (
            returncode == 75
            and payload.get("transient") is True
            and payload.get("retryable") is True
            and payload.get("committed") is False
            and failed is None
        ):
            failed = target
        else:
            raise RuntimeError("reference initial pair had an unexpected outcome")
    if len(results) != 1 or failed is None:
        raise RuntimeError("reference initial pair did not have one partial failure")

    retry = start(failed)
    returncode, payload = collect(retry, failed)
    if returncode != 0 or not successful_result(payload):
        raise RuntimeError("reference selective retry did not return a result")
    results[failed] = payload

    lines: list[str] = []
    for target in TARGETS:
        payload = results[target]
        order, location, availability_date = target
        if (
            payload["order"] != order
            or payload["location"] != location
            or payload["date"] != availability_date
        ):
            raise RuntimeError("executable returned a mismatched order record")
        word = "available" if payload["available"] else "unavailable"
        lines.append(f"- {order} in {location} is {word}.")
    lines.append(f"- Retry: {failed[0]} succeeded after one retry.")
    return "\n".join(lines) + "\n"


def main() -> int:
    if not REPORT.is_file():
        return 0
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    expected = exercise_reference()
    if REPORT.read_text(encoding="utf-8") != expected:
        raise RuntimeError(
            "reference patch report is not grounded in orderctl responses"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
