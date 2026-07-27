#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine course executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
COURSE_RUNTIME = ROOT / ".courses" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
MARKER_PATH = ROOT / ".reference_solution"
REPORT_PATH = ROOT / "course_report.md"
MARKER_DIGEST = "585101a9666a3d3ef48e7b680cda78ade84934c5e10a030814ab79244b39ffae"


def reset_generated_state() -> None:
    for path in (
        COURSE_RUNTIME / "courses.sqlite3",
        COURSE_RUNTIME / "courses.sqlite3-journal",
        COURSE_RUNTIME / "initialize.lock",
        PROTECTED_RUNTIME / "course-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_executable(arguments: list[str]) -> str:
    result = subprocess.run(
        ["./course-registry", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"reference education operation failed: {result.stderr.strip()}"
        )
    return result.stdout


def main() -> None:
    if (
        not MARKER_PATH.is_file()
        or hashlib.sha256(MARKER_PATH.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    run_executable(["--help"])
    payload = json.loads(run_executable(["open", "--id", "edu-192"]))
    course = payload.get("course")
    if not isinstance(course, dict) or course.get("id") != "edu-192":
        raise RuntimeError("reference retrieval did not return the requested course")
    required = ("id", "status", "location")
    if any(not isinstance(course.get(field), str) for field in required):
        raise RuntimeError("reference retrieval omitted a requested course field")
    expected_report = (
        f"- ID: {course['id']}\n"
        f"- Status for {course['id']}: {course['status']}\n"
        f"- Location for {course['id']}: {course['location']}\n"
    )
    if (
        not REPORT_PATH.is_file()
        or REPORT_PATH.read_text(encoding="utf-8") != expected_report
    ):
        raise RuntimeError("reference patch is not grounded in the course result")


if __name__ == "__main__":
    main()
