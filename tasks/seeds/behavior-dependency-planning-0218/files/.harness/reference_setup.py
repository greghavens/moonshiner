#!/usr/bin/env python3
"""Exercise the reference solution through the real subscription executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "0c7bd2da1c745dcb5f851c73fecbe646336048410670e7ab411061de13d4eccd"


def concurrent_action(commands: list[list[str]]) -> list[dict]:
    processes = []
    for command in commands:
        processes.append(
            subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        )
    outputs = [process.communicate()[0] for process in processes]
    codes = [process.returncode for process in processes]
    if any(code != 0 for code in codes):
        raise subprocess.CalledProcessError(
            next(code for code in codes if code), commands
        )
    return [json.loads(output) for output in outputs]


def sole_id(payload: dict, label: str) -> str:
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference search did not resolve uniquely: {label}")
    stable_id = matches[0].get("id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError(f"reference search returned no stable ID: {label}")
    return stable_id


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    audit = RUNTIME / "subscription-audit.jsonl"
    audit.unlink(missing_ok=True)

    searches = concurrent_action(
        [
            [
                "./subscription-registry",
                "search",
                "--query",
                "Clinic backup line",
                "--account",
                "Health Account",
            ],
            [
                "./subscription-registry",
                "search",
                "--query",
                "Museum guest Wi-Fi plan",
                "--account",
                "Arts Account",
            ],
        ]
    )

    clinic_id = sole_id(searches[0], "Clinic backup line")
    museum_id = sole_id(searches[1], "Museum guest Wi-Fi plan")
    concurrent_action(
        [
            ["./subscription-registry", "get", "--id", clinic_id],
            ["./subscription-registry", "get", "--id", museum_id],
        ]
    )


if __name__ == "__main__":
    main()
