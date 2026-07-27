#!/usr/bin/env python3
"""Exercise the reference solution through the genuine library executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / ".library" / "records.json"
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "ddae7ed72c89760ef2ac6d1872c7cbe6d4d226fba5c9e74046a73fdca93e378e"


BASELINE = {
    "records": [
        {
            "id": "lib-253",
            "location": "Bookmobile",
            "name": "Moonlight over Alder Bay",
            "status": "available",
        },
        {
            "id": "lib-653",
            "location": "Central Branch",
            "name": "A Short History of Type",
            "status": "on-loan",
        },
        {
            "id": "lib-1053",
            "location": "East Branch",
            "name": "Moonlight over Alder Bay archive",
            "status": "closed",
        },
    ],
    "notifications": [],
}


def concurrent_action(jobs: list[tuple[list[str], Path]]) -> None:
    streams = []
    processes = []
    try:
        for command, output_path in jobs:
            stream = output_path.open("w", encoding="utf-8")
            streams.append(stream)
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=stream,
                    start_new_session=True,
                )
            )
        codes = [process.wait() for process in processes]
        if any(code != 0 for code in codes):
            raise subprocess.CalledProcessError(
                next(code for code in codes if code), [job[0] for job in jobs]
            )
    finally:
        for stream in streams:
            stream.close()


def returned_status(path: Path, expected_id: str) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    record = payload.get("record")
    if not isinstance(record, dict) or record.get("id") != expected_id:
        raise RuntimeError(f"reference retrieval returned the wrong record: {path.name}")
    status = record.get("status")
    if not isinstance(status, str) or not status:
        raise RuntimeError(f"reference retrieval returned no status: {path.name}")
    return status


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    DATA_PATH.write_text(json.dumps(BASELINE, indent=2) + "\n", encoding="utf-8")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "library-audit.jsonl").unlink(missing_ok=True)

    first_get = RUNTIME / "reference-get-lib-253.json"
    second_get = RUNTIME / "reference-get-lib-653.json"
    concurrent_action(
        [
            (["./library-records", "get", "--id", "lib-253"], first_get),
            (["./library-records", "get", "--id", "lib-653"], second_get),
        ]
    )

    first_status = returned_status(first_get, "lib-253")
    second_status = returned_status(second_get, "lib-653")
    updates: list[tuple[list[str], Path]] = []
    if first_status == "available":
        updates.append(
            (
                [
                    "./library-records",
                    "update",
                    "--id",
                    "lib-253",
                    "--if-status",
                    first_status,
                    "--status",
                    "on-hold",
                ],
                RUNTIME / "reference-update-lib-253.json",
            )
        )
    if second_status == "on-loan":
        updates.append(
            (
                [
                    "./library-records",
                    "update",
                    "--id",
                    "lib-653",
                    "--if-status",
                    second_status,
                    "--status",
                    "available",
                ],
                RUNTIME / "reference-update-lib-653.json",
            )
        )
    if updates:
        concurrent_action(updates)


if __name__ == "__main__":
    main()
