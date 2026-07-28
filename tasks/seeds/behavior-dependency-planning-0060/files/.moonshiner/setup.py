#!/usr/bin/env python3
"""Build the deterministic fleet registry and clear prior task artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = Path(__file__).resolve().with_name("fleet_seed.sql")
DATABASE_PATH = ROOT / "fleet.db"
CLIENT_PATH = ROOT / "fleetctl"
RUNTIME_PATH = ROOT / ".fleet-audit"
DELIVERABLE_PATH = ROOT / "audit.md"


def main() -> int:
    temporary = ROOT / f".fleet-setup-{os.getpid()}.db"
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise RuntimeError("generated fleet database failed integrity check")
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, DATABASE_PATH)
    shutil.rmtree(RUNTIME_PATH, ignore_errors=True)
    DELIVERABLE_PATH.unlink(missing_ok=True)
    CLIENT_PATH.chmod(0o755)

    # Full reference validation applies reference_fix.patch before setup. The
    # optional driver exists only in that patched workspace and proves the
    # requested behavior through genuine fleetctl executions.
    reference_driver = ROOT / "reference_driver.py"
    if reference_driver.is_file():
        subprocess.run(
            [sys.executable, "-B", str(reference_driver)],
            cwd=ROOT,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
