#!/usr/bin/env python3
"""Protected deterministic acceptance gate for the single-file Java client."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path.cwd()
BUILD = ROOT / ".verify-build"


def check_contract_provenance() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    if contract.get("contract_kind") != "reference-documentation-derived":
        raise AssertionError("contract provenance marker changed")
    notice = contract.get("source_notice", "")
    if "not a published API specification" not in notice:
        raise AssertionError("contract must plainly distinguish reference docs from a spec")

    operations = [item["operation"] for item in contract["operations"]]
    source_operations = [item["operation"] for item in sources["sources"]]
    if source_operations != operations:
        raise AssertionError("official source ledger must cover every contract operation")
    if sources.get("date_fetched") != "2026-08-16":
        raise AssertionError("official source fetch date changed")
    for operation, item in zip(contract["operations"], sources["sources"], strict=True):
        if item.get("date_fetched") != "2026-08-16":
            raise AssertionError("each official source needs its fetch date")
        if not item.get("url", "").startswith("https://developer.broadcom.com/xapis/"):
            raise AssertionError("contract source is not an official Broadcom xAPI page")
        if operation.get("source_url") != item["url"]:
            raise AssertionError("contract operation and source ledger URL differ")


def check_single_production_source() -> None:
    sources = sorted(
        path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*.java")
    )
    if sources != ["TestMain.java", "VcfAutomationClient.java"]:
        raise AssertionError(
            "the repository must contain one production Java source plus TestMain.java: "
            + repr(sources)
        )


def run() -> None:
    check_contract_provenance()
    check_single_production_source()
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir()
    try:
        subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-encoding",
                "UTF-8",
                "-d",
                str(BUILD),
                "VcfAutomationClient.java",
                "TestMain.java",
            ],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(
            ["java", "-cp", str(BUILD), "TestMain"],
            cwd=ROOT,
            check=True,
        )
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)


if __name__ == "__main__":
    run()
