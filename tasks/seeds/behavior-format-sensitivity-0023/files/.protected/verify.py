#!/usr/bin/env python3
"""Protected deterministic verifier for the reservation wrapper task."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "retrieve_reservation.sh"
RESERVATIONCTL = ROOT / "bin" / "reservationctl"
DATA_PATH = ROOT / "data" / "reservations.json"
BASELINE_PATH = ROOT / ".protected" / "baseline.sha256"
TARGET_ID = "hos-123"


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
    expected_names = {"bin/reservationctl", "data/reservations.json"}
    if set(baseline) != expected_names:
        return "protected checksum manifest is malformed"
    for relative, expected in baseline.items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            return f"supplied file changed: {relative}"
    return None


def load_expected_record() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reservations = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(reservations, list):
        raise ValueError("reservation fixture is not a list")
    matches = [
        record
        for record in reservations
        if isinstance(record, dict) and record.get("id") == TARGET_ID
    ]
    if len(matches) != 1:
        raise ValueError("target reservation fixture is not unique")
    return reservations, matches[0]


def check_wrapper_source() -> str | None:
    if not WRAPPER.is_file():
        return "retrieve_reservation.sh is missing"
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
    logical_source = source.replace("\\\n", "")
    statements = [
        line.strip()
        for line in logical_source.splitlines()[1:]
        if line.strip() and not line.lstrip().startswith("#")
    ]
    tokenized: list[tuple[str, list[str]]] = []
    try:
        for statement in statements:
            tokenized.append(
                (statement, shlex.split(statement, comments=True, posix=True))
            )
    except ValueError as error:
        return f"wrapper cannot be parsed as POSIX shell: {error}"
    invocations = [
        (statement, tokens)
        for statement, tokens in tokenized
        if any("reservationctl" in token for token in tokens)
    ]
    if len(invocations) != 1:
        return "wrapper must contain exactly one reservationctl invocation"
    invocation, tokens = invocations[0]
    if (
        len(tokens) != 6
        or tokens[0] != "exec"
        or not (
            tokens[1] == "bin/reservationctl"
            or tokens[1].endswith("/bin/reservationctl")
        )
        or tokens[2:5] != ["reservation", "get", "--id"]
    ):
        return "wrapper must directly exec the supplied reservation executable"
    if tokens[5] != TARGET_ID:
        return "wrapper must make one direct retrieval using the requested literal ID"
    literal_id = re.search(
        r"""--id[ \t]+(?:hos-123|'hos-123'|"hos-123")[ \t]*(?:#.*)?$""",
        invocation,
    )
    if literal_id is None:
        return "wrapper must make one direct retrieval using the requested literal ID"
    active_tokens = " ".join(
        token for _, tokens in tokenized for token in tokens
    )
    if re.search(r"\b(?:python(?:[0-9.]+)?|lookup_reservation)\b", active_tokens):
        return "wrapper contains Python indirection"
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


def exercise(cwd: Path, expected_stdout: str, expected_digest: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="reservation-audit-") as temporary:
        audit_path = Path(temporary) / "events.jsonl"
        environment = os.environ.copy()
        environment["RESERVATIONCTL_AUDIT"] = str(audit_path)
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
            return "wrapper emitted prose or diagnostics on stderr"
        try:
            events = read_audit(audit_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return f"reservation execution evidence is invalid: {error}"
        if len(events) != 1:
            return f"expected exactly one reservation data operation, observed {len(events)}"
        event = events[0]
        if (
            event.get("operation") != "get"
            or event.get("id") != TARGET_ID
            or not isinstance(event.get("id"), str)
            or event.get("found") is not True
            or event.get("record_sha256") != expected_digest
        ):
            return "execution evidence does not match the requested reservation"
    return None


def main() -> int:
    problem = check_protected_inputs()
    if problem:
        return fail(problem)
    problem = check_wrapper_source()
    if problem:
        return fail(problem)
    try:
        reservations_before, expected_record = load_expected_record()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return fail(f"cannot load protected reservation state: {error}")
    canonical = json.dumps(
        expected_record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    expected_stdout = canonical + "\n"
    expected_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    data_before = DATA_PATH.read_bytes()

    with tempfile.TemporaryDirectory(prefix="reservation-cwd-") as outside:
        problem = exercise(Path(outside), expected_stdout, expected_digest)
    if problem:
        return fail(problem)

    if DATA_PATH.read_bytes() != data_before:
        return fail("reservation records changed during this read-only task")
    reservations_after = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if reservations_after != reservations_before:
        return fail("reservation state changed during verification")
    if sha256(RESERVATIONCTL) != load_baseline()["bin/reservationctl"]:
        return fail("reservation executable changed during verification")
    print("PASS: requested literal single-operation reservation retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
