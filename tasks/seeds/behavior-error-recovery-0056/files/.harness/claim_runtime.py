#!/usr/bin/env python3
"""Executable implementation for the sandboxed claim-availability client."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / ".claims" / "availability.json"
RUNTIME = ROOT / ".harness" / "runtime"
AUDIT_PATH = RUNTIME / "claim-checks.jsonl"
STATE_PATH = RUNTIME / "failure-state.json"
LOCK_PATH = RUNTIME / "state.lock"
AUDIT_KEY = b"moonshiner-pi-claim-recovery-0056-v1"
CHECK_DELAY_SECONDS = 0.40


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="claim-availability")
    operations = top.add_subparsers(dest="operation", required=True)
    check = operations.add_parser("check", help="check one claims-review slot")
    check.add_argument("--name", required=True)
    check.add_argument("--office", required=True)
    check.add_argument("--date", required=True)
    return top


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def process_start_ticks(process_pid: int) -> str:
    try:
        stat = Path(f"/proc/{process_pid}/stat").read_text(encoding="utf-8")
        return stat.rsplit(") ", 1)[1].split()[19]
    except (OSError, IndexError):
        return "unavailable"


def append_audit(event: dict) -> None:
    signed = dict(event)
    signed["signature"] = hmac.new(
        AUDIT_KEY, canonical(event), hashlib.sha256
    ).hexdigest()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps(signed, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def key_for(name: str, office: str, date: str) -> str:
    return json.dumps([name, office, date], separators=(",", ":"))


def decide(name: str, office: str, date: str) -> tuple[bool | None, str | None, bool]:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    matches = [
        row
        for row in payload["claims"]
        if row.get("name") == name
        and row.get("office") == office
        and row.get("date") == date
    ]
    if len(matches) != 1:
        return None, "lookup_not_unique", False

    RUNTIME.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if STATE_PATH.is_file():
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        else:
            state = {}

        matching_rules = [
            rule
            for rule in payload.get("transient_failures", [])
            if rule.get("name") == name
            and rule.get("office") == office
            and rule.get("date") == date
        ]
        if len(matching_rules) > 1:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return None, "invalid_failure_policy", False

        transient = False
        if matching_rules:
            request_key = key_for(name, office, date)
            used = state.get(request_key, 0)
            limit = matching_rules[0].get("failures", 0)
            if isinstance(used, int) and isinstance(limit, int) and used < limit:
                state[request_key] = used + 1
                temporary = STATE_PATH.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, STATE_PATH)
                transient = True
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    if transient:
        return None, "temporary_unavailable", True
    available = matches[0].get("available")
    if not isinstance(available, bool):
        return None, "invalid_availability", False
    return available, None, False


def main() -> int:
    args = parser().parse_args()
    start_ns = time.monotonic_ns()
    parent_pid = os.getppid()
    available: bool | None = None
    error_code: str | None = None
    retryable = False

    try:
        available, error_code, retryable = decide(args.name, args.office, args.date)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        error_code = "service_error"
        retryable = False

    time.sleep(CHECK_DELAY_SECONDS)
    end_ns = time.monotonic_ns()
    event = {
        "version": 1,
        "event_id": str(uuid.uuid4()),
        "operation": args.operation,
        "name": args.name,
        "office": args.office,
        "date": args.date,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "process_pid": os.getpid(),
        "process_start_ticks": process_start_ticks(os.getpid()),
        "process_group_id": os.getpgrp(),
        "session_id": os.getsid(0),
        "parent_pid": parent_pid,
        "parent_start_ticks": process_start_ticks(parent_pid),
        "success": error_code is None,
    }
    if error_code is None:
        event["available"] = available
    else:
        event["error_code"] = error_code
        event["retryable"] = retryable
    append_audit(event)

    if error_code is None:
        print(
            json.dumps(
                {
                    "name": args.name,
                    "office": args.office,
                    "date": args.date,
                    "available": available,
                },
                sort_keys=True,
            )
        )
        return 0

    print(
        json.dumps({"error": error_code, "retryable": retryable}, sort_keys=True),
        file=sys.stderr,
    )
    return 75 if retryable else 2


if __name__ == "__main__":
    raise SystemExit(main())
