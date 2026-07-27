#!/usr/bin/env python3
"""Exercise the reference solution through the real facilities executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
REPORT = ROOT / "operations_check.md"
REPORT_DIGEST = "12a08dbdedb0447473de1f9d6114cee938aac450a169b4e1471140a17c443a8f"


def concurrent_action(jobs: list[tuple[list[str], Path]]) -> None:
    streams = []
    processes = []
    try:
        for command, output_path in jobs:
            stream = output_path.open("w", encoding="utf-8")
            streams.append(stream)
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=stream,
                    start_new_session=True,
                )
            )
        codes = [process.wait() for process in processes]
        if any(code != 0 for code in codes):
            raise subprocess.CalledProcessError(
                next(code for code in codes if code), jobs
            )
    finally:
        for stream in streams:
            stream.close()


def sole_id(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference search did not resolve uniquely: {path.name}")
    stable_id = matches[0].get("id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError(f"reference search returned no stable ID: {path.name}")
    return stable_id


def main() -> None:
    if (
        not REPORT.is_file()
        or hashlib.sha256(REPORT.read_bytes()).hexdigest() != REPORT_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    audit = RUNTIME / "facility-audit.jsonl"
    audit.unlink(missing_ok=True)

    loading_search = RUNTIME / "reference-search-loading.json"
    signage_search = RUNTIME / "reference-search-signage.json"
    loading_get = RUNTIME / "reference-get-loading.json"
    signage_get = RUNTIME / "reference-get-signage.json"
    temporary_outputs = [loading_search, signage_search, loading_get, signage_get]
    try:
        concurrent_action(
            [
                (
                    [
                        "./facility-registry",
                        "search",
                        "--query",
                        "Loading dock door inspection",
                        "--location",
                        "Warehouse",
                    ],
                    loading_search,
                ),
                (
                    [
                        "./facility-registry",
                        "search",
                        "--query",
                        "Quiet room signage installation",
                        "--location",
                        "Main Office",
                    ],
                    signage_search,
                ),
            ]
        )

        loading_id = sole_id(loading_search)
        signage_id = sole_id(signage_search)
        concurrent_action(
            [
                (
                    ["./facility-registry", "get", "--id", loading_id],
                    loading_get,
                ),
                (
                    ["./facility-registry", "get", "--id", signage_id],
                    signage_get,
                ),
            ]
        )
    finally:
        for path in temporary_outputs:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
