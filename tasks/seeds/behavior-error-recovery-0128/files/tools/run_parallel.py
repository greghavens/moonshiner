#!/usr/bin/env python3
"""Launch two genuine shipping commands concurrently and record one batch."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "evidence"
TRACE_PATH = EVIDENCE_DIR / "availability_trace.jsonl"
SHIPPING_TOOL = (ROOT / "tools" / "shipping.py").resolve()
SEPARATOR = ":::"


def usage() -> str:
    return (
        "usage: python3 tools/run_parallel.py -- COMMAND_A ::: COMMAND_B\n"
        "\nLaunch exactly two repository-local shipping.py commands concurrently.\n"
        "The literal token ::: separates the two argv lists."
    )


def split_commands(values: list[str]) -> list[list[str]]:
    if values and values[0] == "--":
        values = values[1:]
    if values in (["-h"], ["--help"]):
        print(usage())
        raise SystemExit(0)
    if values.count(SEPARATOR) != 1:
        raise ValueError("provide two commands separated by the literal token :::")
    position = values.index(SEPARATOR)
    commands = [values[:position], values[position + 1 :]]
    if any(not command for command in commands):
        raise ValueError("neither command may be empty")
    for command in commands:
        if len(command) < 2 or Path(command[1]).resolve() != SHIPPING_TOOL:
            raise ValueError("each command must invoke the repository shipping.py tool")
    return commands


def next_batch() -> int:
    if not TRACE_PATH.exists():
        return 1
    rows = [
        json.loads(line)
        for line in TRACE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    numbers = [row["batch"] for row in rows if isinstance(row.get("batch"), int)]
    return max(numbers, default=0) + 1


def append_batch(batch: dict[str, Any]) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    with TRACE_PATH.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(batch, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        fcntl.flock(handle, fcntl.LOCK_UN)


def main() -> int:
    try:
        commands = split_commands(sys.argv[1:])
    except ValueError as error:
        print(f"run_parallel.py: {error}\n{usage()}", file=sys.stderr)
        return 2

    batch_number = next_batch()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    capture_paths = [
        EVIDENCE_DIR / f".capture-{batch_number}-{slot}.json" for slot in range(2)
    ]
    for path in capture_paths:
        if path.exists():
            path.unlink()

    processes: list[subprocess.Popen[str]] = []
    for command, capture_path in zip(commands, capture_paths, strict=True):
        environment = os.environ.copy()
        environment["SHIPPING_CAPTURE"] = str(capture_path)
        processes.append(
            subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    completed = [process.communicate() for process in processes]
    payloads: list[dict[str, Any]] = []
    exit_codes: list[int] = []
    capture_failure = False
    for slot, (process, (stdout, stderr), capture_path) in enumerate(
        zip(processes, completed, capture_paths, strict=True), start=1
    ):
        exit_codes.append(process.returncode)
        if stdout:
            print(f"[parallel slot {slot} stdout]\n{stdout.rstrip()}")
        if stderr:
            print(f"[parallel slot {slot} stderr]\n{stderr.rstrip()}", file=sys.stderr)
        if not capture_path.exists():
            print(f"parallel slot {slot} produced no shipping evidence", file=sys.stderr)
            capture_failure = True
            continue
        payloads.append(json.loads(capture_path.read_text(encoding="utf-8")))
        capture_path.unlink()

    if capture_failure:
        return 1

    append_batch(
        {
            "batch": batch_number,
            "parallel": True,
            "commands": payloads,
            "exit_codes": exit_codes,
        }
    )
    if all(code == 0 for code in exit_codes):
        return 0
    if any(code == 75 for code in exit_codes):
        return 75
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
