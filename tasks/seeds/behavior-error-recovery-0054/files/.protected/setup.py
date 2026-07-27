#!/usr/bin/env python3
"""Reset runtime state and execute the applied reference solution when present."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".expense-runtime"
DELIVERABLE = ROOT / "availability-report.md"
CLIENT = ROOT / "expense-availability"
TARGETS = (
    {
        "name": "Train fare",
        "location": "Chicago",
        "date": "2026-09-27",
    },
    {
        "name": "Team lunch",
        "location": "Boston",
        "date": "2026-09-27",
    },
)


def command_for(target: dict[str, str]) -> list[str]:
    return [
        str(CLIENT),
        "availability",
        "--name",
        target["name"],
        "--location",
        target["location"],
        "--date",
        target["date"],
    ]


def bash_command(arguments: list[str]) -> list[str]:
    return ["bash", "-c", shlex.join(arguments)]


def collect(process: subprocess.Popen[str]) -> dict[str, Any]:
    stdout, stderr = process.communicate(timeout=5)
    response_text = stdout if process.returncode == 0 else stderr
    try:
        response = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise RuntimeError("expense executable returned invalid JSON") from error
    return {"returncode": process.returncode, "response": response}


def run_one(target: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(
        bash_command(command_for(target)),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    response_text = completed.stdout if completed.returncode == 0 else completed.stderr
    try:
        response = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise RuntimeError("expense retry returned invalid JSON") from error
    return {"returncode": completed.returncode, "response": response}


def run_reference_solution() -> None:
    subprocess.run(
        bash_command([str(CLIENT), "--help"]),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    processes = [
        subprocess.Popen(
            bash_command(command_for(target)),
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for target in TARGETS
    ]
    initial = [collect(process) for process in processes]

    if initial[0]["returncode"] != 0:
        raise RuntimeError("reference independent branch did not succeed")
    if initial[1]["returncode"] != 75:
        raise RuntimeError("reference retry branch did not transiently fail")
    if initial[1]["response"].get("retryable") is not True:
        raise RuntimeError("reference failure was not explicitly retryable")

    retried = run_one(TARGETS[1])
    if retried["returncode"] != 0:
        raise RuntimeError("reference retry did not succeed")

    results = [initial[0]["response"], retried["response"]]
    lines = []
    for result in results:
        availability = "available" if result["available"] else "unavailable"
        lines.append(
            f"- {result['name']} at {result['location']}: {availability} "
            f"on {result['date']}."
        )
    DELIVERABLE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    reference_solution_applied = DELIVERABLE.is_file()
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    if DELIVERABLE.exists():
        DELIVERABLE.unlink()
    if reference_solution_applied:
        run_reference_solution()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
