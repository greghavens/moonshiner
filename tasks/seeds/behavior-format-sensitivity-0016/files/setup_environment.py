#!/usr/bin/env python3
"""Initialize the disposable SQLite state used by the claim executable."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "__pycache__"
DATABASE = RUNTIME / "insurance.sqlite3"


def main() -> int:
    shutil.rmtree(RUNTIME, ignore_errors=True)
    RUNTIME.mkdir()
    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(
            (ROOT / "insurance.sql").read_text(encoding="utf-8")
        )
        database.execute("PRAGMA journal_mode = WAL")
        database.commit()
    finally:
        database.close()

    # Reference validation applies reference_fix.patch before setup. When its
    # artifact is present, reproduce it through the same one-get executable
    # flow without adding a task-authored helper to the patched workspace.
    artifact = ROOT / "lookup-call.json"
    if artifact.is_file():
        request = ET.parse(ROOT / "queue" / "lookup.xml").getroot()
        record_id = request.findtext("record-id")
        mode = request.findtext("mode")
        if not record_id or not mode:
            raise RuntimeError("queue request is incomplete")
        completed = subprocess.run(
            [
                "./insurancectl",
                "get",
                "--id",
                record_id,
                "--mode",
                mode,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        artifact.write_text(completed.stdout, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
