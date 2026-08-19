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


def _usable(path: Path) -> bool:
    """Whether the registry already on disk can be read and is intact."""
    if not path.is_file():
        return False
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error:
        return False
    try:
        return connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    except sqlite3.Error:
        return False
    finally:
        connection.close()


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
    # Only when there is nothing usable there already. SQLite does not write
    # the same bytes twice for the same statements, so replacing a database
    # that was already correct left the workspace differing from the one the
    # seed ships -- and the workspace is what the agent is handed, because
    # this setup runs for reference validation and never for a trace.
    if _usable(DATABASE_PATH):
        temporary.unlink(missing_ok=True)
    else:
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
