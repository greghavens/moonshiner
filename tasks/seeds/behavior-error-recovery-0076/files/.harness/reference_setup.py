#!/usr/bin/env python3
"""Exercise the reference behavior through the genuine claim executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "a921807edb9ad93703e78bf17c0d80b869896266365e82cbff5fcb5e2624bcf0"
REPORT = ROOT / "availability-report.md"
TARGETS = [
    ("Theft claim — gallery camera", "West Office"),
    ("Windshield claim — fleet van", "North Office"),
]
DATE = "2026-08-07"


def command(claim: str, office: str) -> list[str]:
    return [
        "./claim-availability",
        "check",
        "--claim",
        claim,
        "--office",
        office,
        "--date",
        DATE,
    ]


def parse_payload(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"reference output was not an object: {path.name}")
    return payload


def concurrent_initial_checks() -> list[tuple[int, Path, Path]]:
    streams = []
    processes = []
    try:
        for index, (claim, office) in enumerate(TARGETS):
            stdout_path = RUNTIME / f"reference-initial-{index}.stdout"
            stderr_path = RUNTIME / f"reference-initial-{index}.stderr"
            stdout = stdout_path.open("w", encoding="utf-8")
            stderr = stderr_path.open("w", encoding="utf-8")
            streams.extend([stdout, stderr])
            process = subprocess.Popen(
                command(claim, office),
                cwd=ROOT,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            processes.append((process, stdout_path, stderr_path))
        return [
            (process.wait(), stdout_path, stderr_path)
            for process, stdout_path, stderr_path in processes
        ]
    finally:
        for stream in streams:
            stream.close()


def retry(claim: str, office: str) -> tuple[int, Path]:
    stdout_path = RUNTIME / "reference-retry.stdout"
    stderr_path = RUNTIME / "reference-retry.stderr"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        result = subprocess.run(
            command(claim, office),
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            check=False,
            start_new_session=True,
        )
    return result.returncode, stdout_path


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    for path in RUNTIME.iterdir():
        if path.is_file() or path.is_symlink():
            path.unlink()

    completed = concurrent_initial_checks()
    succeeded = [index for index, item in enumerate(completed) if item[0] == 0]
    failed = [index for index, item in enumerate(completed) if item[0] != 0]
    if len(succeeded) != 1 or len(failed) != 1:
        raise RuntimeError("reference initial checks did not produce one partial failure")

    failed_index = failed[0]
    failed_code, _, failed_stderr = completed[failed_index]
    failed_payload = parse_payload(failed_stderr)
    if (
        failed_code != 75
        or failed_payload.get("transient") is not True
        or failed_payload.get("retryable") is not True
    ):
        raise RuntimeError("reference failed branch was not retryable and transient")

    retry_code, retry_stdout = retry(*TARGETS[failed_index])
    if retry_code != 0:
        raise RuntimeError("reference failed-branch retry did not succeed")

    successful_index = succeeded[0]
    returned = {
        successful_index: parse_payload(completed[successful_index][1]),
        failed_index: parse_payload(retry_stdout),
    }
    lines = []
    for index in range(len(TARGETS)):
        result = returned[index]
        availability = "available" if result.get("available") is True else "unavailable"
        lines.append(
            f"- {result.get('claim')} in {result.get('office')}: {availability} "
            f"on {result.get('date')}."
        )
    retried = returned[failed_index]
    lines.append(
        f"- Retried branch: {retried.get('claim')} in {retried.get('office')}."
    )
    expected_report = "\n".join(lines) + "\n"
    if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != expected_report:
        raise RuntimeError("reference deliverable is not grounded in executable output")


if __name__ == "__main__":
    main()
