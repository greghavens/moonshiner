#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine fleet executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
FLEET_RUNTIME = ROOT / ".fleet" / "runtime"
PROTECTED_RUNTIME = ROOT / ".protected" / "runtime"
REPORT = ROOT / "fleet_handoff.md"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "f3fdb9ea705bf6f882a2fd19ecfcb260c710c5dceb5ef89b321fc9a733c95e09"
TARGETS = (
    ("Bus 14 museum charter", "Depot E"),
    ("EV 6 facilities inspection", "Depot F"),
)


def reset_generated_state() -> None:
    for path in (FLEET_RUNTIME, PROTECTED_RUNTIME):
        if path.exists():
            shutil.rmtree(path)
    REPORT.unlink(missing_ok=True)


def concurrent_action(commands: list[list[str]]) -> list[dict[str, object]]:
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )
        for command in commands
    ]
    results: list[dict[str, object]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        if process.returncode != 0:
            raise RuntimeError(
                "reference fleet operation failed: " + (stderr.strip() or stdout.strip())
            )
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("reference fleet operation returned invalid JSON")
        results.append(payload)
    return results


def sole_id(payload: dict[str, object], target: tuple[str, str]) -> str:
    matches = payload.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError(f"reference lookup did not resolve uniquely: {target!r}")
    match = matches[0]
    if not isinstance(match, dict):
        raise RuntimeError(f"reference lookup returned an invalid match: {target!r}")
    vehicle_id = match.get("id")
    if (
        not isinstance(vehicle_id, str)
        or not vehicle_id
        or match.get("name") != target[0]
        or match.get("location") != target[1]
    ):
        raise RuntimeError(f"reference lookup returned no matching stable ID: {target!r}")
    return vehicle_id


def complete_record(
    payload: dict[str, object],
    target: tuple[str, str],
    vehicle_id: str,
) -> dict[str, str]:
    record = payload.get("record")
    if not isinstance(record, dict):
        raise RuntimeError(f"reference retrieval returned no record: {target!r}")
    if (
        record.get("id") != vehicle_id
        or record.get("name") != target[0]
        or record.get("location") != target[1]
        or not isinstance(record.get("status"), str)
        or not record.get("status")
        or not isinstance(record.get("date"), str)
        or not record.get("date")
    ):
        raise RuntimeError(f"reference retrieval returned a mismatched record: {target!r}")
    return {key: str(value) for key, value in record.items()}


def date_relation(first: str, second: str) -> str:
    if first == second:
        return "the same as"
    return "earlier than" if first < second else "later than"


def write_report(records: list[dict[str, str]]) -> None:
    first, second = records
    status_relation = "match" if first["status"] == second["status"] else "differ"
    REPORT.write_text(
        f'- {first["name"]} | {first["location"]} | ID {first["id"]} | '
        f'status {first["status"]} | date {first["date"]}\n'
        f'- {second["name"]} | {second["location"]} | ID {second["id"]} | '
        f'status {second["status"]} | date {second["date"]}\n'
        f'- Comparison | statuses {status_relation} '
        f'({first["status"]} vs {second["status"]}) | {first["name"]} is dated '
        f'{date_relation(first["date"], second["date"])} {second["name"]} '
        f'({first["date"]} vs {second["date"]})\n',
        encoding="utf-8",
    )


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    subprocess.run(["./fleetctl", "--help"], cwd=ROOT, check=True, capture_output=True)
    search_results = concurrent_action(
        [
            ["./fleetctl", "search", "--name", name, "--location", location]
            for name, location in TARGETS
        ]
    )
    vehicle_ids = [
        sole_id(payload, target)
        for payload, target in zip(search_results, TARGETS, strict=True)
    ]
    retrieval_results = concurrent_action(
        [["./fleetctl", "get", "--id", vehicle_id] for vehicle_id in vehicle_ids]
    )
    records = [
        complete_record(payload, target, vehicle_id)
        for payload, target, vehicle_id in zip(
            retrieval_results,
            TARGETS,
            vehicle_ids,
            strict=True,
        )
    ]
    write_report(records)


if __name__ == "__main__":
    main()
