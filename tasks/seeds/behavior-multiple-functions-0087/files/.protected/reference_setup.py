#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine inventory executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_RUNTIME = ROOT / ".inventory" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
MARKER_PATH = ROOT / ".reference_solution"
MARKER_DIGEST = "d72a4acd4514cbc7d4182834921186f716bc409b3f0272c71cc9445affe85d25"


def reset_generated_state() -> None:
    for path in (
        INVENTORY_RUNTIME / "inventory.sqlite3",
        INVENTORY_RUNTIME / "inventory.sqlite3-shm",
        INVENTORY_RUNTIME / "inventory.sqlite3-wal",
        INVENTORY_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "inventory-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_executable(arguments: list[str]) -> str:
    result = subprocess.run(
        [
            "bash",
            "-c",
            '"$1" "${@:2}"\n'
            "operation_status=$?\n"
            'exit "$operation_status"',
            "reference-inventory-interaction",
            "./inventoryctl",
            *arguments,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"reference inventory operation failed: {result.stderr.strip()}"
        )
    return result.stdout


def main() -> None:
    if (
        not MARKER_PATH.is_file()
        or hashlib.sha256(MARKER_PATH.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    run_executable(["--help"])
    payload = json.loads(
        run_executable(
            [
                "search",
                "--name",
                "Packing tape case",
                "--location",
                "Warehouse C",
            ]
        )
    )
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError("reference lookup did not resolve to exactly one match")


if __name__ == "__main__":
    main()
