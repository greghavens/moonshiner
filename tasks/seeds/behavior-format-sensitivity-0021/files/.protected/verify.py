#!/usr/bin/env python3
"""Protected deterministic verifier for the calendar wrapper task."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "open_meeting.sh"
CALENDARCTL = ROOT / "bin" / "calendarctl"
DATA_PATH = ROOT / "data" / "meetings.json"
BASELINE_PATH = ROOT / ".protected" / "baseline.sha256"
TARGET_ID = "cal-121"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def load_baseline() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        values[relative] = digest
    return values


def check_protected_inputs() -> str | None:
    baseline = load_baseline()
    expected_names = {"bin/calendarctl", "data/meetings.json"}
    if set(baseline) != expected_names:
        return "protected checksum manifest is malformed"
    for relative, expected in baseline.items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            return f"supplied file changed: {relative}"
    return None


def load_expected_record() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meetings = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(meetings, list):
        raise ValueError("meeting fixture is not a list")
    matches = [
        record
        for record in meetings
        if isinstance(record, dict) and record.get("id") == TARGET_ID
    ]
    if len(matches) != 1:
        raise ValueError("target meeting fixture is not unique")
    return meetings, matches[0]


def check_wrapper_source() -> str | None:
    if not WRAPPER.is_file():
        return "open_meeting.sh is missing"
    source = WRAPPER.read_text(encoding="utf-8")
    if "\r" in source:
        return "wrapper must use POSIX line endings"
    if not source.startswith("#!/bin/sh\n"):
        return "wrapper must retain a POSIX sh shebang"
    syntax = subprocess.run(
        ["sh", "-n", str(WRAPPER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if syntax.returncode != 0:
        return f"wrapper has invalid shell syntax: {syntax.stderr.strip()}"
    if source.count("calendarctl") != 1:
        return "wrapper must contain exactly one calendarctl invocation"
    invocation = next(
        (line.strip() for line in source.splitlines() if "calendarctl" in line),
        "",
    )
    direct_lookup = re.search(
        r"""calendarctl["']?\s+meeting\s+get\s+--id(?:\s+|=)(?:(["'])cal-121\1|cal-121)(?:\s*)$""",
        invocation,
    )
    if direct_lookup is None:
        return "wrapper must make one direct get using the literal string ID"
    forbidden_invocation = (
        "$(",
        "`",
        " eval ",
        " sh -c ",
        " bash -c ",
        " search ",
        " list ",
        " update ",
    )
    padded_invocation = f" {invocation} "
    if any(token in padded_invocation for token in forbidden_invocation):
        return "wrapper contains derivation, commentary, or a forbidden operation"
    if re.search(r"(?m)^\s*(?:printf|echo)(?:\s|$)", source):
        return "wrapper must not emit shell-generated commentary"
    return None


def read_audit(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError("audit event is not an object")
        events.append(payload)
    return events


def exercise(
    cwd: Path, expected_stdout: str, expected_digest: str
) -> str | None:
    with tempfile.TemporaryDirectory(prefix="calendar-audit-") as temporary:
        audit_path = Path(temporary) / "events.jsonl"
        environment = os.environ.copy()
        environment["CALENDARCTL_AUDIT"] = str(audit_path)
        completed = subprocess.run(
            [str(WRAPPER)],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if completed.returncode != 0:
            return f"wrapper exited {completed.returncode}: {completed.stderr.strip()}"
        if completed.stdout != expected_stdout:
            return "wrapper did not pass through the native record output unchanged"
        if completed.stderr != "":
            return "wrapper emitted commentary or diagnostics on stderr"
        try:
            events = read_audit(audit_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return f"calendar execution evidence is invalid: {error}"
        if len(events) != 1:
            return f"expected exactly one calendar data operation, observed {len(events)}"
        event = events[0]
        if (
            event.get("operation") != "get"
            or event.get("id") != TARGET_ID
            or not isinstance(event.get("id"), str)
            or event.get("found") is not True
            or event.get("record_sha256") != expected_digest
        ):
            return "calendar execution evidence does not match the requested lookup"
    return None


def main() -> int:
    problem = check_protected_inputs()
    if problem:
        return fail(problem)
    problem = check_wrapper_source()
    if problem:
        return fail(problem)
    try:
        meetings_before, expected_record = load_expected_record()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return fail(f"cannot load protected calendar state: {error}")
    canonical_record = json.dumps(
        expected_record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    expected_stdout = canonical_record + "\n"
    expected_digest = hashlib.sha256(canonical_record.encode("utf-8")).hexdigest()
    data_before = DATA_PATH.read_bytes()

    problem = exercise(ROOT, expected_stdout, expected_digest)
    if problem:
        return fail(problem)
    with tempfile.TemporaryDirectory(prefix="calendar-cwd-") as outside:
        problem = exercise(Path(outside), expected_stdout, expected_digest)
    if problem:
        return fail(problem)

    if DATA_PATH.read_bytes() != data_before:
        return fail("calendar records changed during this read-only task")
    meetings_after = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if meetings_after != meetings_before:
        return fail("calendar state changed during verification")
    if sha256(CALENDARCTL) != load_baseline()["bin/calendarctl"]:
        return fail("calendar executable changed during verification")
    print("PASS: literal single-operation meeting lookup verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
