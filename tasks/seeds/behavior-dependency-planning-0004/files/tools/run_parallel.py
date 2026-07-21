#!/usr/bin/env python3
"""Launch exactly two real commands concurrently and record one audit batch."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TRACE_PATH = ROOT / "evidence" / "audit_trace.jsonl"
SEPARATOR = ":::"


def usage() -> str:
    return (
        "usage: python3 tools/run_parallel.py -- COMMAND_A ::: COMMAND_B\n"
        "\nLaunch exactly two repository-local commands concurrently. Each command\n"
        "must be a messages.py operation and may contain its own quoted arguments."
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
    return commands


def prior_batches() -> list[dict[str, Any]]:
    if not TRACE_PATH.exists():
        return []
    records = []
    for line in TRACE_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def append_batch(batch: dict[str, Any]) -> None:
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
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

    existing = prior_batches()
    numeric_batches = [row["batch"] for row in existing if isinstance(row.get("batch"), int)]
    batch_number = max(numeric_batches, default=0) + 1

    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    capture_paths = [
        TRACE_PATH.parent / f".capture-{batch_number}-{slot}.json" for slot in range(2)
    ]
    for path in capture_paths:
        if path.exists():
            path.unlink()

    processes = []
    for command, capture_path in zip(commands, capture_paths, strict=True):
        environment = os.environ.copy()
        environment["COMM_AUDIT_CAPTURE"] = str(capture_path)
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
    failures = []
    for slot, (process, (stdout, stderr), capture_path) in enumerate(
        zip(processes, completed, capture_paths, strict=True), start=1
    ):
        print(f"[parallel slot {slot}]\n{stdout.rstrip()}")
        if stderr:
            print(stderr.rstrip(), file=sys.stderr)
        if process.returncode != 0:
            failures.append(f"slot {slot} exited {process.returncode}")
        if not capture_path.exists():
            failures.append(f"slot {slot} produced no service result")
            continue
        payloads.append(json.loads(capture_path.read_text(encoding="utf-8")))
        capture_path.unlink()

    if failures:
        print("; ".join(failures), file=sys.stderr)
        return 1

    append_batch({"batch": batch_number, "parallel": True, "commands": payloads})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
