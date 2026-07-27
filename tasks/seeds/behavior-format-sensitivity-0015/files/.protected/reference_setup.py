#!/usr/bin/env python3
"""Exercise the reference solution through genuine executable execution."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".reference_solution"
AUDIT_PATH = ROOT / ".protected" / "runtime" / "appointment-audit.jsonl"
MARKER_DIGEST = "287647ecbe9e804d8184790fbab30752ae563a835ec280bdb1e465cacdbee9eb"
EXPECTED_FIELDS = {
    "attendee",
    "clinician",
    "date",
    "description",
    "duration_minutes",
    "id",
    "lifecycle",
    "location",
    "name",
    "status",
    "visit_type",
}


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    AUDIT_PATH.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment["MOONSHINER_REFERENCE_RUN"] = "1"
    completed = subprocess.run(
        ["./appointment-record", "get", "--id", "hea-115"],
        cwd=ROOT,
        env=environment,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    payload = json.loads(completed.stdout)
    record = payload.get("appointment")
    if (
        not isinstance(record, dict)
        or record.get("id") != "hea-115"
        or set(record) != EXPECTED_FIELDS
    ):
        raise RuntimeError("reference execution did not return the complete appointment")


if __name__ == "__main__":
    main()
