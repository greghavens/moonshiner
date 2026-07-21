#!/usr/bin/env python3
"""Local claims queue command used by the protected acceptance environment.

The command operates on an actual tab-separated record store chosen with
CLAIMS_RECORDS.  Each command invocation appends process-level evidence to the
JSONL file named by CLAIMS_AUDIT.  Search and get are deliberately separate so
a caller cannot obtain report fields from the discovery response.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import time


FIELDS = ("id", "name", "location", "status", "date")
READ_ONLY = {"search", "get", "list", "profile", "availability"}
MUTATING = {"create", "update", "cancel", "notify"}


def _path_from_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return Path(value)


def _read_records() -> list[dict[str, str]]:
    path = _path_from_env("CLAIMS_RECORDS")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise RuntimeError("claims record store has an invalid header")
        return [{key: row[key] for key in FIELDS} for row in reader]


def _write_records(records: list[dict[str, str]]) -> None:
    path = _path_from_env("CLAIMS_RECORDS")
    temporary = path.with_suffix(path.suffix + ".new")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    temporary.replace(path)


def _audit(op: str, phase: str, **detail: object) -> None:
    path = _path_from_env("CLAIMS_AUDIT")
    event = {
        "run": os.environ.get("CLAIMS_RUN_ID", "default"),
        "op": op,
        "phase": phase,
        "entrypoint": __name__,
        "pid": os.getpid(),
        "time_ns": time.monotonic_ns(),
        **detail,
    }
    payload = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def _events() -> list[dict[str, object]]:
    path = _path_from_env("CLAIMS_AUDIT")
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            events.append(json.loads(line))
    return events


def _same_run_events() -> list[dict[str, object]]:
    run_id = os.environ.get("CLAIMS_RUN_ID", "default")
    return [event for event in _events() if event.get("run") == run_id]


def _wait_for_starts(op: str, count: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        starts = [event for event in _same_run_events()
                  if event.get("op") == op and event.get("phase") == "start"]
        if len({event.get("pid") for event in starts}) >= count:
            return True
        time.sleep(0.01)
    return False


def _completed_searches() -> list[dict[str, object]]:
    return [event for event in _same_run_events()
            if event.get("op") == "search" and event.get("phase") == "end"]


def _eligible_ids() -> list[str]:
    identifiers = []
    for event in _completed_searches():
        stable_ids = event.get("stable_ids")
        if (isinstance(stable_ids, list) and len(stable_ids) == 1
                and isinstance(stable_ids[0], str) and stable_ids[0]):
            identifiers.append(stable_ids[0])
    return identifiers


def _emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")))


def _run_search(args: argparse.Namespace) -> int:
    detail = {"name": args.name, "location": args.location}
    _audit("search", "start", **detail)
    if not _wait_for_starts("search", 2):
        _audit("search", "error", reason="search stage was not concurrent", **detail)
        print("claim search stage requires both searches to run concurrently",
              file=sys.stderr)
        return 70
    matches = [record["id"] for record in _read_records()
               if record["name"] == args.name
               and record["location"] == args.location]
    result = {"stable_ids": matches}
    _audit("search", "end", stable_ids=matches, **detail)
    _emit(result)
    return 0


def _run_get(args: argparse.Namespace) -> int:
    completed = _completed_searches()
    if len(completed) != 2:
        _audit("get", "error", id=args.id,
               reason="get started before both searches completed")
        print("both searches must complete before retrieval", file=sys.stderr)
        return 70
    eligible = _eligible_ids()
    if args.id not in eligible:
        _audit("get", "error", id=args.id,
               reason="id was not the sole result of its search")
        print("get id is not eligible from completed searches", file=sys.stderr)
        return 65
    _audit("get", "start", id=args.id)
    if len(eligible) > 1 and not _wait_for_starts("get", len(eligible)):
        _audit("get", "error", id=args.id,
               reason="eligible gets were not concurrent")
        print("eligible gets must run concurrently", file=sys.stderr)
        return 70
    matches = [record for record in _read_records() if record["id"] == args.id]
    if len(matches) != 1:
        _audit("get", "error", id=args.id, reason="stable id lookup failed")
        print("stable id lookup failed", file=sys.stderr)
        return 66
    result = matches[0]
    _audit("get", "end", id=args.id)
    _emit(result)
    return 0


def _run_other(args: argparse.Namespace) -> int:
    op = args.command
    _audit(op, "start")
    records = _read_records()
    if op == "list":
        result: object = records
    elif op == "profile":
        profile_path = os.environ.get("CLAIMS_PREFERENCES")
        result = (json.loads(Path(profile_path).read_text(encoding="utf-8"))
                  if profile_path and Path(profile_path).exists() else {})
    elif op == "availability":
        result = {"records": len(records)}
    elif op == "create":
        if any(record["id"] == args.id for record in records):
            raise RuntimeError("claim id already exists")
        records.append({key: getattr(args, key) for key in FIELDS})
        _write_records(records)
        result = {"created": args.id}
    elif op == "update":
        matches = [record for record in records if record["id"] == args.id]
        if len(matches) != 1:
            raise RuntimeError("claim id not found")
        matches[0]["status"] = args.status
        _write_records(records)
        result = {"updated": args.id}
    elif op == "cancel":
        matches = [record for record in records if record["id"] == args.id]
        if len(matches) != 1:
            raise RuntimeError("claim id not found")
        matches[0]["status"] = "cancelled"
        _write_records(records)
        result = {"cancelled": args.id}
    elif op == "notify":
        notice_path = Path(os.environ.get("CLAIMS_NOTIFICATIONS",
                                          str(_path_from_env("CLAIMS_AUDIT").with_suffix(".notices"))))
        with notice_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": args.id, "message": args.message}) + "\n")
        result = {"notified": args.id}
    else:  # pragma: no cover - argparse owns the command set
        raise RuntimeError(f"unknown command: {op}")
    _audit(op, "end")
    _emit(result)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute one operation against the local claims review queue.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser(
        "search", help="find stable IDs by exact claim name and location")
    search.add_argument("--name", required=True)
    search.add_argument("--location", required=True)

    get = subparsers.add_parser(
        "get", help="retrieve one complete claim by stable ID")
    get.add_argument("--id", required=True)

    subparsers.add_parser("list", help="list every claim")
    subparsers.add_parser("profile", help="read saved queue preferences")
    subparsers.add_parser("availability", help="check queue availability")

    create = subparsers.add_parser("create", help="create a claim")
    for field in FIELDS:
        create.add_argument(f"--{field}", required=True)
    update = subparsers.add_parser("update", help="update a claim status")
    update.add_argument("--id", required=True)
    update.add_argument("--status", required=True)
    cancel = subparsers.add_parser("cancel", help="cancel a claim")
    cancel.add_argument("--id", required=True)
    notify = subparsers.add_parser("notify", help="send a claim notification")
    notify.add_argument("--id", required=True)
    notify.add_argument("--message", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "search":
            return _run_search(args)
        if args.command == "get":
            return _run_get(args)
        return _run_other(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        try:
            _audit(args.command, "error", reason=str(error))
        except (OSError, RuntimeError):
            pass
        print(f"claims_cli.py: {error}", file=sys.stderr)
        return 74


if __name__ == "__main__":
    raise SystemExit(main())
