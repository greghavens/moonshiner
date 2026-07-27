#!/usr/bin/env python3
"""Exercise the reference solution through the genuine claims executable."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".protected" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "e2f9388d98c32d6f769a7cee34cd720d2ae1a7edcac094503d9157faa60e442b"


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "claims-audit.jsonl").unlink(missing_ok=True)
    completed = subprocess.run(
        [
            "./claims-registry",
            "list",
            "--status",
            "active",
            "--location",
            "North Office",
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
