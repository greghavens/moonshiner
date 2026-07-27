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
AUDIT_PATH = ROOT / ".protected" / "runtime" / "project-task-audit.jsonl"
MARKER_DIGEST = "00c70b11e70f49617fc3b71510dc1844f6f82e8cafb2feda055c42e12d37985e"
EXPECTED_FIELDS = {
    "assignee",
    "description",
    "due_on",
    "id",
    "lifecycle",
    "location",
    "name",
    "priority",
    "project",
    "status",
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
        ["./project-task", "get", "--id", "pro-110"],
        cwd=ROOT,
        env=environment,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    payload = json.loads(completed.stdout)
    record = payload.get("task")
    if (
        not isinstance(record, dict)
        or record.get("id") != "pro-110"
        or set(record) != EXPECTED_FIELDS
    ):
        raise RuntimeError("reference execution did not return the complete task")


if __name__ == "__main__":
    main()
