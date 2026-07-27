#!/usr/bin/env python3
"""Exercise the reference solution through the genuine catalog executable."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".protected" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "63b9fd66f7de28d9f132721ee8217bf50f49c35cf46483b332dcd63826680ade"


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "catalog-audit.jsonl").unlink(missing_ok=True)
    completed = subprocess.run(
        [
            "./catalog-registry",
            "list",
            "--status",
            "active",
            "--location",
            "East Branch",
            "--sort",
            "name",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    (RUNTIME / "reference-output.json").write_text(
        completed.stdout, encoding="utf-8"
    )


if __name__ == "__main__":
    main()
