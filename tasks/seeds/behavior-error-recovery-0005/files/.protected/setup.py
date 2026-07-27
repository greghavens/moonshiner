#!/usr/bin/env python3
"""Initialize the disposable support sandbox for one trace."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".support-runtime"
DATABASE = RUNTIME / "support.sqlite3"
SEED = ROOT / ".protected" / "support_seed.sql"
RECEIPT = ROOT / "cancellation.receipt.json"
REPORT = ROOT / "cancellation-result.txt"
CLIENT = ROOT / "supportctl"
TARGET_ID = "sup-275"


def run_reference_workflow() -> None:
    cancellation = subprocess.run(
        [str(CLIENT), "cancel", "--id", TARGET_ID],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if cancellation.returncode != 124:
        raise RuntimeError(
            "reference cancellation did not exercise the client deadline"
        )
    if "commit state is unknown" not in cancellation.stderr:
        raise RuntimeError("reference cancellation did not leave uncertain state")

    retrieval = subprocess.run(
        [str(CLIENT), "get", "--id", TARGET_ID],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if retrieval.returncode != 0:
        raise RuntimeError(retrieval.stderr.strip() or "reference retrieval failed")
    try:
        payload: Any = json.loads(retrieval.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("reference retrieval returned invalid JSON") from error
    record = payload.get("record") if isinstance(payload, dict) else None
    if not isinstance(record, dict):
        raise RuntimeError("reference retrieval returned no complete record")
    if record.get("id") != TARGET_ID or record.get("status") != "cancelled":
        raise RuntimeError("reference retrieval did not verify the target cancellation")
    if not RECEIPT.is_file():
        raise RuntimeError("support executable did not issue its receipt")


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)
    if RECEIPT.exists():
        RECEIPT.unlink()

    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        database.execute("PRAGMA journal_mode = WAL")
        database.commit()
    finally:
        database.close()

    if REPORT.is_file():
        run_reference_workflow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
