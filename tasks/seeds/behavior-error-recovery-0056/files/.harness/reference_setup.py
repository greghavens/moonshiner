#!/usr/bin/env python3
"""Exercise the reference solution through the real availability executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "a998035da2fbfa9bafc6a80734c3bcdde56def90b4527bc46355455f9fc4ea3f"
COMMANDS = [
    "./claim-availability check --name 'Theft claim' --office 'West Office' --date 2026-11-11",
    "./claim-availability check --name 'Windshield claim' --office 'North Office' --date 2026-11-11",
]


def run_initial() -> list[int]:
    processes = []
    streams = []
    try:
        for index, command in enumerate(COMMANDS):
            stdout = (RUNTIME / f"reference-initial-{index}.out").open(
                "w", encoding="utf-8"
            )
            stderr = (RUNTIME / f"reference-initial-{index}.err").open(
                "w", encoding="utf-8"
            )
            streams.extend([stdout, stderr])
            processes.append(
                subprocess.Popen(
                    ["bash", "-c", command],
                    cwd=ROOT,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
            )
        return [process.wait() for process in processes]
    finally:
        for stream in streams:
            stream.close()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    for name in ("claim-checks.jsonl", "failure-state.json", "state.lock"):
        (RUNTIME / name).unlink(missing_ok=True)

    codes = run_initial()
    if sorted(codes) != [0, 75]:
        raise RuntimeError(f"reference initial action returned unexpected statuses: {codes}")
    failed_index = codes.index(75)
    error = load_json(RUNTIME / f"reference-initial-{failed_index}.err")
    if error != {"error": "temporary_unavailable", "retryable": True}:
        raise RuntimeError("reference failure was not the executable's retryable transient")
    successful_index = 1 - failed_index
    success = load_json(RUNTIME / f"reference-initial-{successful_index}.out")
    if not isinstance(success.get("available"), bool):
        raise RuntimeError("reference successful branch returned no availability Boolean")

    with (RUNTIME / "reference-retry.out").open("w", encoding="utf-8") as stdout:
        with (RUNTIME / "reference-retry.err").open("w", encoding="utf-8") as stderr:
            subprocess.run(
                ["bash", "-c", COMMANDS[failed_index]],
                cwd=ROOT,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                check=True,
            )
    recovered = load_json(RUNTIME / "reference-retry.out")
    if not isinstance(recovered.get("available"), bool):
        raise RuntimeError("reference retry returned no availability Boolean")


if __name__ == "__main__":
    main()
