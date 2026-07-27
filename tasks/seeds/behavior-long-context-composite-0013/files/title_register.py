#!/usr/bin/env python3
"""Local command-line client for the sandboxed title register."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "register_data" / "titles.json"
KEY_PATH = ROOT / "register_data" / "audit.key"
RUNTIME = ROOT / ".register-runtime"
AUDIT_PATH = RUNTIME / "audit.jsonl"
STATE_PATH = RUNTIME / "state.json"
OUTBOX_PATH = RUNTIME / "notifications.jsonl"


def load_records() -> list[dict]:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise RuntimeError("register data is malformed")
    return records


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"status_overrides": {}}
    value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("register state is malformed")
    return value


def effective_record(record: dict) -> dict:
    result = dict(record)
    override = load_state().get("status_overrides", {}).get(record["id"])
    if override is not None:
        result["status"] = override
    return result


def canonical(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def audit(operation: str, arguments: dict, result_ids: list[str]) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    sequence = 1
    if AUDIT_PATH.exists():
        sequence += sum(1 for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines()
                        if line.strip())
    event = {
        "sequence": sequence,
        "operation": operation,
        "arguments": arguments,
        "result_ids": result_ids,
    }
    key = KEY_PATH.read_bytes().strip()
    event["signature"] = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
    with AUDIT_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def find_by_id(stable_id: str) -> dict | None:
    for record in load_records():
        if record.get("id") == stable_id:
            return effective_record(record)
    return None


def command_search(args: argparse.Namespace) -> int:
    matches = [
        effective_record(record)
        for record in load_records()
        if record.get("name") == args.name
        and record.get("location") == args.location
        and effective_record(record).get("status") == "active"
    ]
    snippets = [
        {"id": record["id"], "name": record["name"], "location": record["location"]}
        for record in matches
    ]
    audit("search", {"name": args.name, "location": args.location},
          [record["id"] for record in matches])
    print(json.dumps({"count": len(snippets), "matches": snippets},
                     ensure_ascii=False, sort_keys=True))
    return 0


def command_get(args: argparse.Namespace) -> int:
    record = find_by_id(args.id)
    audit("get", {"id": args.id}, [record["id"]] if record else [])
    if record is None:
        print(json.dumps({"error": "title not found"}, sort_keys=True))
        return 4
    print(json.dumps({"record": record}, ensure_ascii=False, sort_keys=True))
    return 0


def command_list(_: argparse.Namespace) -> int:
    records = [effective_record(record) for record in load_records()]
    audit("list", {}, [record["id"] for record in records])
    print(json.dumps({"records": records}, ensure_ascii=False, sort_keys=True))
    return 0


def command_availability(args: argparse.Namespace) -> int:
    record = find_by_id(args.id)
    audit("availability", {"id": args.id}, [record["id"]] if record else [])
    if record is None:
        print(json.dumps({"error": "title not found"}, sort_keys=True))
        return 4
    print(json.dumps(
        {"id": record["id"], "available": record["status"] == "active"},
        sort_keys=True,
    ))
    return 0


def command_set_status(args: argparse.Namespace) -> int:
    record = find_by_id(args.id)
    audit("set-status", {"id": args.id, "status": args.status},
          [record["id"]] if record else [])
    if record is None:
        print(json.dumps({"error": "title not found"}, sort_keys=True))
        return 4
    state = load_state()
    state.setdefault("status_overrides", {})[args.id] = args.status
    RUNTIME.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"id": args.id, "status": args.status}, sort_keys=True))
    return 0


def command_notify(args: argparse.Namespace) -> int:
    record = find_by_id(args.id)
    audit("notify", {"id": args.id, "message": args.message},
          [record["id"]] if record else [])
    if record is None:
        print(json.dumps({"error": "title not found"}, sort_keys=True))
        return 4
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with OUTBOX_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(
            {"id": args.id, "message": args.message}, sort_keys=True
        ) + "\n")
    print(json.dumps({"notification": "queued", "id": args.id}, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query and administer the local title register."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser(
        "search", help="exactly search active titles by name and location"
    )
    search.add_argument("--name", required=True)
    search.add_argument("--location", required=True)
    search.set_defaults(handler=command_search)

    get = subparsers.add_parser("get", help="retrieve one full record by stable ID")
    get.add_argument("--id", required=True)
    get.set_defaults(handler=command_get)

    listing = subparsers.add_parser("list", help="list the complete register")
    listing.set_defaults(handler=command_list)

    availability = subparsers.add_parser(
        "availability", help="check whether a title is available"
    )
    availability.add_argument("--id", required=True)
    availability.set_defaults(handler=command_availability)

    set_status = subparsers.add_parser(
        "set-status", help="change a title's status"
    )
    set_status.add_argument("--id", required=True)
    set_status.add_argument("--status", required=True)
    set_status.set_defaults(handler=command_set_status)

    notify = subparsers.add_parser("notify", help="queue a title notification")
    notify.add_argument("--id", required=True)
    notify.add_argument("--message", required=True)
    notify.set_defaults(handler=command_notify)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"title register error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
