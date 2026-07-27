#!/usr/bin/env python3
"""Reset deterministic runtime evidence for the trip archive task."""

from __future__ import annotations

import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / ".protected" / "archive.json"
RUNTIME = ROOT / ".trip-runtime"


def main() -> int:
    document = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    records = document.get("records")
    if not isinstance(records, list) or not records:
        raise SystemExit("protected archive is invalid")
    stable_ids = [record.get("stable_id") for record in records]
    if any(not isinstance(value, str) or not value for value in stable_ids):
        raise SystemExit("protected archive contains an invalid stable ID")
    if len(stable_ids) != len(set(stable_ids)):
        raise SystemExit("protected archive contains duplicate stable IDs")
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
