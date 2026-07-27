#!/usr/bin/env python3
"""Reset deterministic evidence and perform the reference lookup when requested."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / ".protected" / "subscriptions.json"
CLIENT = ROOT / "telecom-console"
REFERENCE_STATE = ROOT / ".reference-state"
RUNTIME = ROOT / ".telecom-runtime"


def target_id() -> str:
    document = json.loads(DATA.read_text(encoding="utf-8"))
    records = document.get("records")
    if not isinstance(records, list) or not records:
        raise SystemExit("protected subscription catalog is invalid")
    stable_ids = [record.get("stable_id") for record in records if isinstance(record, dict)]
    if len(stable_ids) != len(records):
        raise SystemExit("protected subscription catalog contains an invalid record")
    if any(not isinstance(value, str) or not value for value in stable_ids):
        raise SystemExit("protected subscription catalog contains an invalid stable ID")
    if len(stable_ids) != len(set(stable_ids)):
        raise SystemExit("protected subscription catalog contains duplicate stable IDs")
    target = document.get("verification_target")
    if not isinstance(target, str) or target not in stable_ids:
        raise SystemExit("protected subscription catalog has an invalid audit target")
    return target


def main(argv: list[str]) -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    stable_id = target_id()
    if not argv:
        return 0
    if argv != ["--reference-run"]:
        raise SystemExit("usage: setup.py [--reference-run]")
    if REFERENCE_STATE.read_text(encoding="utf-8") != "reference\n":
        raise SystemExit("reference run requires the applied reference patch")
    expression = f"telecom_get(id={json.dumps(stable_id, ensure_ascii=False)})"
    completed = subprocess.run(
        [str(CLIENT), expression],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
