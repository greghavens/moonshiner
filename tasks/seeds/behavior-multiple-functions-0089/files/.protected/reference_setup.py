#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine CRM executable."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CRM_RUNTIME = ROOT / ".crm" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
REPORT_PATH = ROOT / "account_report.md"


def reset_generated_state() -> None:
    for path in (
        CRM_RUNTIME / "accounts.sqlite3",
        CRM_RUNTIME / "accounts.sqlite3-journal",
        CRM_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "crm-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_executable(arguments: list[str]) -> str:
    result = subprocess.run(
        ["./crmctl", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"reference CRM operation failed: {result.stderr.strip()}"
        )
    return result.stdout


def main() -> None:
    if not REPORT_PATH.is_file():
        return

    reset_generated_state()
    run_executable(["--help"])
    payload = json.loads(run_executable(["open", "--id", "crm-189"]))
    account = payload.get("account")
    if not isinstance(account, dict) or account.get("id") != "crm-189":
        raise RuntimeError("reference retrieval did not return the requested account")
    required = ("name", "status", "location", "scheduled_date")
    if any(not isinstance(account.get(field), str) for field in required):
        raise RuntimeError("reference retrieval omitted a requested account field")
    expected_report = (
        f"{account['name']} | status: {account['status']} | "
        f"location: {account['location']} | "
        f"scheduled date: {account['scheduled_date']}\n"
    )
    if (
        not REPORT_PATH.is_file()
        or REPORT_PATH.read_text(encoding="utf-8") != expected_report
    ):
        raise RuntimeError("reference patch is not grounded in the CRM result")


if __name__ == "__main__":
    main()
