#!/usr/bin/env python3
"""Exercise the reference behavior through the genuine course executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "4c50e2aba2a92aceddef576eeabe1cbbba7e702b9a472faebf915764b6dfea3e"
REPORT = ROOT / "availability.txt"
COURSES = [
    ("Microeconomics evening seminar", "Downtown Campus"),
    ("Modern history survey", "North Campus"),
]
DATE = "2026-08-17"


def command(course: str, campus: str) -> list[str]:
    return [
        "./course-availability",
        "check",
        "--course",
        course,
        "--campus",
        campus,
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
        for index, (course, campus) in enumerate(COURSES):
            stdout_path = RUNTIME / f"reference-initial-{index}.stdout"
            stderr_path = RUNTIME / f"reference-initial-{index}.stderr"
            stdout = stdout_path.open("w", encoding="utf-8")
            stderr = stderr_path.open("w", encoding="utf-8")
            streams.extend([stdout, stderr])
            process = subprocess.Popen(
                command(course, campus),
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


def retry(course: str, campus: str) -> tuple[int, Path]:
    stdout_path = RUNTIME / "reference-retry.stdout"
    stderr_path = RUNTIME / "reference-retry.stderr"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        result = subprocess.run(
            command(course, campus),
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
        if path.is_file():
            path.unlink()

    completed = concurrent_initial_checks()
    succeeded = [index for index, item in enumerate(completed) if item[0] == 0]
    failed = [index for index, item in enumerate(completed) if item[0] != 0]
    if len(succeeded) != 1 or len(failed) != 1:
        raise RuntimeError("reference initial checks did not produce one partial failure")

    failed_index = failed[0]
    failed_code, _, failed_stderr = completed[failed_index]
    failed_payload = parse_payload(failed_stderr)
    if failed_code != 75 or failed_payload.get("transient") is not True:
        raise RuntimeError("reference failed branch was not explicitly transient")

    retry_code, retry_stdout = retry(*COURSES[failed_index])
    if retry_code != 0:
        raise RuntimeError("reference failed-branch retry did not succeed")

    returned = {}
    successful_index = succeeded[0]
    returned[COURSES[successful_index][0]] = parse_payload(
        completed[successful_index][1]
    ).get("availability")
    returned[COURSES[failed_index][0]] = parse_payload(retry_stdout).get(
        "availability"
    )
    expected_report = "".join(
        f"{course}: {returned[course]}\n" for course, _ in COURSES
    )
    if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != expected_report:
        raise RuntimeError("reference deliverable is not grounded in executable output")


if __name__ == "__main__":
    main()
