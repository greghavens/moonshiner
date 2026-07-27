#!/usr/bin/env python3
"""Exercise the reference solution through the real shipment executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "4de1c4df7a5ffac281547694d8b7ae22ae85fb4ee7caddfff5a8e1bc7194aab8"


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "shipping-audit.jsonl").unlink(missing_ok=True)
    completed = subprocess.run(
        [
            "./shipping-registry",
            "list",
            "--status",
            "active",
            "--location",
            "Portland Hub",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    records = payload.get("records")
    if not isinstance(records, list) or len(records) != 2:
        raise RuntimeError("reference structured list did not return two records")


if __name__ == "__main__":
    main()
