#!/usr/bin/env python3
"""Exercise the reference answer through the genuine CRM executable."""

from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CRM_RUNTIME = ROOT / ".crm" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
OUTPUT_PATH = ROOT / "account-opened.txt"
EXPECTED_OUTPUT = "crm-129 | Arbor Foods 029 | West | active\n"


def reset_generated_state() -> None:
    for path in (
        CRM_RUNTIME / "accounts.sqlite3",
        CRM_RUNTIME / "accounts.sqlite3-shm",
        CRM_RUNTIME / "accounts.sqlite3-wal",
        CRM_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "crm-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def main() -> None:
    if (
        not OUTPUT_PATH.is_file()
        or OUTPUT_PATH.read_text(encoding="utf-8") != EXPECTED_OUTPUT
    ):
        return

    reset_generated_state()
    result = subprocess.run(
        ["./crm-account", "crm-129"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"reference CRM invocation failed: {result.stderr.strip()}")
    if OUTPUT_PATH.read_text(encoding="utf-8") != EXPECTED_OUTPUT:
        raise RuntimeError("reference CRM invocation produced the wrong account output")


if __name__ == "__main__":
    main()
