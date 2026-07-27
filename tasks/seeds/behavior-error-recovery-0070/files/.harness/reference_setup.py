#!/usr/bin/env python3
"""Exercise the reference solution through the real registry executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "48ca13bcc7a79a3d16194cd3c6ea4f887f75ff47a110f33ebc25ae6c4a75cc1c"


def command(name: str, location: str) -> list[str]:
    return [
        "./availability-registry",
        "check",
        "--name",
        name,
        "--location",
        location,
        "--date",
        "2026-08-19",
    ]


def concurrent_initial_checks() -> tuple[int, int]:
    jobs = [
        (
            command("Accessibility audit", "Beacon Program"),
            RUNTIME / "reference-accessibility.stdout",
            RUNTIME / "reference-accessibility.stderr",
        ),
        (
            command("Newsletter redesign", "Clover Program"),
            RUNTIME / "reference-newsletter-first.stdout",
            RUNTIME / "reference-newsletter-first.stderr",
        ),
    ]
    streams = []
    processes = []
    try:
        for argv, stdout_path, stderr_path in jobs:
            stdout = stdout_path.open("w", encoding="utf-8")
            stderr = stderr_path.open("w", encoding="utf-8")
            streams.extend([stdout, stderr])
            processes.append(
                subprocess.Popen(
                    argv,
                    cwd=ROOT,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
            )
        return tuple(process.wait() for process in processes)
    finally:
        for stream in streams:
            stream.close()


def retry_newsletter() -> int:
    with (RUNTIME / "reference-newsletter-retry.stdout").open(
        "w", encoding="utf-8"
    ) as stdout, (RUNTIME / "reference-newsletter-retry.stderr").open(
        "w", encoding="utf-8"
    ) as stderr:
        result = subprocess.run(
            command("Newsletter redesign", "Clover Program"),
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            check=False,
            start_new_session=True,
        )
    return result.returncode


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    for name in (
        "availability-audit.jsonl",
        "attempt-state.json",
        "attempt-state.json.tmp",
        "attempt-state.lock",
    ):
        (RUNTIME / name).unlink(missing_ok=True)

    initial_codes = concurrent_initial_checks()
    if initial_codes != (0, 75):
        raise RuntimeError(f"unexpected initial reference results: {initial_codes}")
    first_payload = json.loads(
        (RUNTIME / "reference-accessibility.stdout").read_text(encoding="utf-8")
    )
    failed_payload = json.loads(
        (RUNTIME / "reference-newsletter-first.stderr").read_text(encoding="utf-8")
    )
    if first_payload.get("availability") != "available":
        raise RuntimeError("reference successful branch returned an unexpected value")
    if failed_payload.get("transient") is not True:
        raise RuntimeError("reference failed branch was not explicitly transient")

    if retry_newsletter() != 0:
        raise RuntimeError("reference failed-branch retry did not succeed")
    retry_payload = json.loads(
        (RUNTIME / "reference-newsletter-retry.stdout").read_text(encoding="utf-8")
    )
    if retry_payload.get("availability") != "unavailable":
        raise RuntimeError("reference retry returned an unexpected value")


if __name__ == "__main__":
    main()
