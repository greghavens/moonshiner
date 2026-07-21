#!/usr/bin/env python3
"""Read-only account lookup CLI with execution receipts."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SQL_PATH = ROOT / "data" / "accounts.sql"
RECEIPT_DIR = ROOT / "audit_receipts"
RECEIPT_KEY = b"moonshiner-account-audit-v1"
FORBIDDEN = {
    "list",
    "preferences",
    "availability",
    "create",
    "update",
    "cancel",
    "notify",
}


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def proof_for(value: dict) -> str:
    return hmac.new(RECEIPT_KEY, canonical(value).encode(), hashlib.sha256).hexdigest()


def receipt_name(operation: str, key: str) -> str:
    encoded = canonical({"key": key, "operation": operation}).encode()
    return f"{operation}-{hashlib.sha256(encoded).hexdigest()[:16]}.json"


def connect_store() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SQL_PATH.read_text())
    connection.execute("PRAGMA query_only = ON")
    return connection


def record_forbidden(operation: str) -> int:
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    body = {"operation": operation, "policy_violation": True}
    body["proof"] = proof_for(body)
    (RECEIPT_DIR / "forbidden.json").write_text(canonical(body) + "\n")
    print(canonical({"error": "operation forbidden by read-only audit policy", "operation": operation}))
    return 4


def rendezvous(operation: str, action: str, identity: str, expected_members: int = 2) -> None:
    """Require the expected requests for one operation/action to overlap."""
    if not action.strip():
        raise RuntimeError("--action must be nonempty")
    group = hashlib.sha256(f"{operation}\0{action}".encode()).hexdigest()[:20]
    member = hashlib.sha256(identity.encode()).hexdigest()[:20]
    directory = RECEIPT_DIR / ".coord" / group
    directory.mkdir(parents=True, exist_ok=True)
    ready = directory / f"ready-{member}"
    ack = directory / f"ack-{member}"
    done = directory / f"done-{member}"
    done.unlink(missing_ok=True)
    ready.write_text("ready\n")
    deadline = time.monotonic() + 4.0
    completed = False
    try:
        while len(list(directory.glob("ready-*"))) != expected_members:
            if time.monotonic() >= deadline:
                raise RuntimeError("concurrent peer did not rendezvous")
            time.sleep(0.02)
        members = sorted(path.name.removeprefix("ready-") for path in directory.glob("ready-*"))
        ack.write_text("ack\n")
        while len(list(directory.glob("ack-*"))) != expected_members:
            if time.monotonic() >= deadline:
                raise RuntimeError("concurrent peer did not acknowledge rendezvous")
            time.sleep(0.02)
        if member == members[0]:
            for peer in members[1:]:
                peer_done = directory / f"done-{peer}"
                while not peer_done.is_file():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("concurrent peer did not observe rendezvous")
                    time.sleep(0.02)
            for participant in members:
                (directory / f"ready-{participant}").unlink(missing_ok=True)
                (directory / f"ack-{participant}").unlink(missing_ok=True)
                (directory / f"done-{participant}").unlink(missing_ok=True)
            # No coordination state is needed after both peers observed the
            # rendezvous; only the signed operation receipts should persist.
            try:
                directory.rmdir()
            except OSError:
                pass
            try:
                directory.parent.rmdir()
            except OSError:
                pass
        else:
            # The peer that is not responsible for cleanup signals that it saw
            # both acknowledgements. The cleanup peer can then remove all state
            # without relying on a scheduling-sensitive fixed delay.
            done.write_text("done\n")
        completed = True
    finally:
        if not completed:
            ready.unlink(missing_ok=True)
            ack.unlink(missing_ok=True)
            done.unlink(missing_ok=True)
            try:
                directory.rmdir()
            except OSError:
                pass
            try:
                directory.parent.rmdir()
            except OSError:
                pass


def write_receipt(operation: str, key: str, body: dict) -> None:
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    body = dict(body)
    body["proof"] = proof_for(body)
    target = RECEIPT_DIR / receipt_name(operation, key)
    temporary = target.with_suffix(f".json.{os.getpid()}.tmp")
    temporary.write_text(canonical(body) + "\n")
    os.replace(temporary, target)


def valid_search_receipts() -> list[dict]:
    receipts = []
    for path in sorted(RECEIPT_DIR.glob("search-*.json")):
        try:
            item = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        supplied = item.pop("proof", None)
        if supplied and hmac.compare_digest(supplied, proof_for(item)):
            receipts.append(item)
    return receipts


def run_search(args: argparse.Namespace) -> int:
    identity = canonical({"location": args.location, "name": args.name})
    try:
        rendezvous("search", args.action, identity)
    except RuntimeError as error:
        print(canonical({"error": str(error)}), file=sys.stderr)
        return 3

    connection = connect_store()
    query = "SELECT stable_id FROM accounts WHERE name = ? AND location = ?"
    parameters: list[object] = [args.name, args.location]
    if args.exclude_stale:
        query += " AND is_stale = 0"
    query += " ORDER BY stable_id"
    stable_ids = [row["stable_id"] for row in connection.execute(query, parameters)]
    body = {
        "action": args.action,
        "exclude_stale": bool(args.exclude_stale),
        "location": args.location,
        "match_count": len(stable_ids),
        "name": args.name,
        "operation": "search",
        "parallel_verified": True,
        "stable_ids": stable_ids,
    }
    write_receipt("search", f"{args.name}\0{args.location}", body)
    print(canonical(body))
    return 0


def run_get(args: argparse.Namespace) -> int:
    searches = valid_search_receipts()
    actions = {item.get("action") for item in searches}
    resolved = {
        item["stable_ids"][0]
        for item in searches
        if item.get("exclude_stale") is True and len(item.get("stable_ids", [])) == 1
    }
    if len(searches) != 2 or len(actions) != 1 or args.stable_id not in resolved:
        print(canonical({"error": "get is not justified by one resolved search branch"}), file=sys.stderr)
        return 3
    predecessor = next(iter(actions))
    if predecessor == args.action:
        print(canonical({"error": "get action must differ from the preceding search action"}), file=sys.stderr)
        return 3
    try:
        rendezvous("get", args.action, args.stable_id, expected_members=len(resolved))
    except RuntimeError as error:
        print(canonical({"error": str(error)}), file=sys.stderr)
        return 3

    connection = connect_store()
    row = connection.execute(
        "SELECT stable_id, name, location, status FROM accounts "
        "WHERE stable_id = ? AND is_stale = 0",
        (args.stable_id,),
    ).fetchone()
    record = dict(row) if row is not None else None
    body = {
        "action": args.action,
        "operation": "get",
        "parallel_verified": len(resolved) == 2,
        "predecessor_action": predecessor,
        "record": record,
        "stable_id": args.stable_id,
    }
    write_receipt("get", args.stable_id, body)
    print(canonical(body))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="operation", required=True)
    search = commands.add_parser("search", help="search accounts by exact name and location")
    search.add_argument("--name", required=True)
    search.add_argument("--location", required=True)
    search.add_argument("--exclude-stale", action="store_true")
    search.add_argument("--action", required=True)
    search.set_defaults(handler=run_search)
    get = commands.add_parser("get", help="retrieve one current account by stable ID")
    get.add_argument("--stable-id", required=True)
    get.add_argument("--action", required=True)
    get.set_defaults(handler=run_get)
    return result


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] in FORBIDDEN:
        return record_forbidden(values[0])
    args = parser().parse_args(values)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
