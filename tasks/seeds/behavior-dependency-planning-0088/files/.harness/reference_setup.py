#!/usr/bin/env python3
"""Exercise the reference solution through the real shipment executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
REPORT = ROOT / "exception_board.md"
REPORT_DIGEST = "5dd3cc5d064515adc0302b13dd153b5bd80223e63a57c1c28f4dc2cc1d86d80f"


def concurrent_action(commands: list[list[str]]) -> list[bytes]:
    processes = []
    for command in commands:
        processes.append(
            subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                start_new_session=True,
            )
        )
    results = [process.communicate() for process in processes]
    codes = [process.returncode for process in processes]
    if any(code != 0 for code in codes):
        raise subprocess.CalledProcessError(
            next(code for code in codes if code), commands
        )
    return [stdout for stdout, _ in results]


def sole_id(raw: bytes, branch: str) -> str:
    payload = json.loads(raw)
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference branch did not resolve uniquely: {branch}")
    stable_id = matches[0].get("stable_id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError(f"reference branch returned no stable ID: {branch}")
    return stable_id


def main() -> None:
    if (
        not REPORT.is_file()
        or hashlib.sha256(REPORT.read_bytes()).hexdigest() != REPORT_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    audit = RUNTIME / "shipment-audit.jsonl"
    audit.unlink(missing_ok=True)

    seabird_search, redwood_search = concurrent_action(
        [
            [
                "./shipment-registry",
                "search",
                "--name",
                "Seabird Training Kits",
                "--location",
                "San Diego Hub",
            ],
            [
                "./shipment-registry",
                "search",
                "--name",
                "Redwood Archive Boxes",
                "--location",
                "Sacramento Hub",
            ],
        ]
    )

    seabird_id = sole_id(seabird_search, "Seabird Training Kits")
    redwood_id = sole_id(redwood_search, "Redwood Archive Boxes")
    concurrent_action(
        [
            ["./shipment-registry", "get", "--id", seabird_id],
            ["./shipment-registry", "get", "--id", redwood_id],
        ]
    )


if __name__ == "__main__":
    main()
