#!/usr/bin/env python3
"""Executable local shipping service for the availability exercise."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SHIPMENTS_PATH = DATA_DIR / "shipments.json"
POLICY_PATH = DATA_DIR / "transient_policy.json"
MUTATION_LOG_PATH = DATA_DIR / "mutation_log.json"
EVIDENCE_DIR = ROOT / "evidence"
TRACE_PATH = EVIDENCE_DIR / "availability_trace.jsonl"
RUNTIME_DIR = ROOT / ".shipping-runtime"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def prior_batches() -> list[dict[str, Any]]:
    if not TRACE_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in TRACE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append_trace(command: dict[str, Any]) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    with TRACE_PATH.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        rows = [json.loads(line) for line in handle if line.strip()]
        numbers = [row["batch"] for row in rows if isinstance(row.get("batch"), int)]
        batch = {
            "batch": max(numbers, default=0) + 1,
            "parallel": False,
            "commands": [command],
        }
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(batch, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        fcntl.flock(handle, fcntl.LOCK_UN)


def emit(command: dict[str, Any], exit_code: int) -> int:
    capture = os.environ.get("SHIPPING_CAPTURE")
    if capture:
        Path(capture).write_text(
            json.dumps(command, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    else:
        append_trace(command)

    if command["status"] == "ok":
        print(json.dumps(command["result"], indent=2, sort_keys=True))
    else:
        print(json.dumps(command["error"], sort_keys=True), file=sys.stderr)
    return exit_code


def arguments_for(args: argparse.Namespace) -> dict[str, Any]:
    if args.action == "availability":
        return {"name": args.name, "location": args.location, "date": args.date}
    if args.action == "get":
        return {"id": args.id}
    if args.action == "list":
        return {"location": args.location}
    if args.action == "search":
        return {"query": args.query}
    if args.action == "create":
        return {
            "id": args.id,
            "name": args.name,
            "location": args.location,
        }
    if args.action in {"update", "cancel"}:
        result = {"id": args.id}
        if args.action == "update":
            result["status"] = args.status
        return result
    raise AssertionError(f"unhandled action: {args.action}")


def transient_failure(arguments: dict[str, Any]) -> dict[str, Any] | None:
    policy = load_json(POLICY_PATH)
    for rule in policy.get("availability_fail_once", []):
        if all(arguments.get(field) == rule.get(field) for field in ("name", "location", "date")):
            digest = hashlib.sha256(
                json.dumps(arguments, sort_keys=True).encode("utf-8")
            ).hexdigest()[:16]
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            marker = RUNTIME_DIR / f"consumed-{digest}"
            try:
                descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                return None
            else:
                os.close(descriptor)
                return {
                    "code": rule["code"],
                    "message": "The availability backend is temporarily unavailable.",
                    "retryable": True,
                }
    return None


def availability(args: argparse.Namespace, shipments: list[dict[str, Any]]) -> int:
    arguments = arguments_for(args)
    time.sleep(0.2)
    failure = transient_failure(arguments)
    if failure is not None:
        return emit(
            {
                "operation": "availability",
                "arguments": arguments,
                "status": "error",
                "error": failure,
            },
            75,
        )

    matches = [
        row
        for row in shipments
        if row["name"] == args.name and row["location"] == args.location
    ]
    if len(matches) != 1:
        return emit(
            {
                "operation": "availability",
                "arguments": arguments,
                "status": "error",
                "error": {
                    "code": "not_uniquely_resolved",
                    "message": "Exact name and location did not resolve one parcel.",
                    "retryable": False,
                },
            },
            4,
        )

    window = matches[0].get("availability", {}).get(args.date)
    if window is None:
        return emit(
            {
                "operation": "availability",
                "arguments": arguments,
                "status": "error",
                "error": {
                    "code": "date_not_offered",
                    "message": "No availability window exists for that date.",
                    "retryable": False,
                },
            },
            4,
        )

    result = {
        "name": args.name,
        "location": args.location,
        "date": args.date,
        "available": window["available"],
        "remaining_capacity": window["remaining_capacity"],
    }
    return emit(
        {
            "operation": "availability",
            "arguments": arguments,
            "status": "ok",
            "result": result,
        },
        0,
    )


def read_operation(
    args: argparse.Namespace, shipments: list[dict[str, Any]]
) -> tuple[dict[str, Any], int]:
    arguments = arguments_for(args)
    if args.action == "get":
        result: Any = next((row for row in shipments if row["id"] == args.id), None)
    elif args.action == "list":
        result = [
            row
            for row in shipments
            if args.location is None or row["location"] == args.location
        ]
    else:
        folded = args.query.casefold()
        result = [
            {"id": row["id"], "name": row["name"], "location": row["location"]}
            for row in shipments
            if folded in row["name"].casefold()
        ]
    command = {
        "operation": args.action,
        "arguments": arguments,
        "status": "ok",
        "result": result,
    }
    return command, 0


def mutate(
    args: argparse.Namespace, shipments: list[dict[str, Any]]
) -> tuple[dict[str, Any], int]:
    arguments = arguments_for(args)
    changed = False
    if args.action == "create":
        if not any(row["id"] == args.id for row in shipments):
            shipments.append(
                {
                    "id": args.id,
                    "name": args.name,
                    "location": args.location,
                    "status": "pending",
                    "availability": {},
                }
            )
            changed = True
    else:
        record = next((row for row in shipments if row["id"] == args.id), None)
        if record is not None:
            record["status"] = "cancelled" if args.action == "cancel" else args.status
            changed = True
    if changed:
        save_json(SHIPMENTS_PATH, shipments)
        mutations = load_json(MUTATION_LOG_PATH)
        mutations.append({"operation": args.action, "arguments": arguments})
        save_json(MUTATION_LOG_PATH, mutations)
    command = {
        "operation": args.action,
        "arguments": arguments,
        "status": "ok",
        "result": {"changed": changed},
    }
    return command, 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Query and maintain the sandboxed local shipping collection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "availability syntax:\n"
            "  python3 tools/shipping.py availability --name NAME "
            "--location LOCATION --date YYYY-MM-DD\n"
            "Run an action with --help for its complete option details."
        ),
    )
    actions = root.add_subparsers(dest="action", required=True)

    check = actions.add_parser(
        "availability", help="Check one exact parcel, location, and date."
    )
    check.add_argument("--name", required=True)
    check.add_argument("--location", required=True)
    check.add_argument("--date", required=True)

    get = actions.add_parser("get", help="Retrieve a full shipment by stable ID.")
    get.add_argument("--id", required=True)

    listing = actions.add_parser("list", help="List shipment records.")
    listing.add_argument("--location")

    search = actions.add_parser("search", help="Search related parcel names.")
    search.add_argument("--query", required=True)

    create = actions.add_parser("create", help="Create a shipment record.")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--location", required=True)

    update = actions.add_parser("update", help="Update a shipment status.")
    update.add_argument("--id", required=True)
    update.add_argument("--status", required=True)

    cancel = actions.add_parser("cancel", help="Cancel a shipment record.")
    cancel.add_argument("--id", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    shipments = load_json(SHIPMENTS_PATH)
    if args.action == "availability":
        return availability(args, shipments)
    if args.action in {"get", "list", "search"}:
        command, exit_code = read_operation(args, shipments)
    else:
        command, exit_code = mutate(args, shipments)
    return emit(command, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
