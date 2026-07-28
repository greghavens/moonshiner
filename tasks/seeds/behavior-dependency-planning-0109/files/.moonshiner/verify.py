#!/usr/bin/env python3
"""Protected verifier for the dependency-planning status audit."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_status_audit.sh"
RECORDS = ROOT / "records"

EXPECTED_NAMES = ("Juniper Bakery", "Northline Pediatrics")
EXPECTED_SEARCHES = {
    ("Juniper Bakery", "West Region"): "rec-7f3a91",
    ("Northline Pediatrics", "Central Region"): "rec-2c8e44",
}
SEED_ROWS = (
    ("rec-7f3a91", "Juniper Bakery", "West Region", "Active", 0),
    ("rec-2c8e44", "Northline Pediatrics", "Central Region", "Pending Review", 0),
    ("rec-33bd10", "Juniper Bakery", "Central Region", "Archived", 0),
    ("rec-ae1098", "Northline Pediatrics", "West Region", "Active", 0),
    ("rec-8b761d", "Copper Kettle Market", "West Region", "Active", 0),
    ("rec-908c22", "Lakeshore Dental", "Central Region", "Inactive", 0),
)
STATUS_CASES = (
    {
        "rec-7f3a91": "Active",
        "rec-2c8e44": "Pending Review",
    },
    {
        "rec-7f3a91": "Ready",
        "rec-2c8e44": "Ready",
    },
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(value: str | None, label: str) -> object:
    if value is None:
        fail(f"{label} was not completed")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        fail(f"{label} did not record valid JSON: {exc}")


def prepare_state(
    state_dir: Path, env: dict[str, str], statuses: dict[str, str]
) -> Path:
    """Initialize the genuine fixture, then choose deterministic test statuses."""
    try:
        bootstrap = subprocess.run(
            [
                str(RECORDS),
                "search",
                "--name",
                "Juniper Bakery",
                "--region",
                "West Region",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f"could not initialize the records fixture: {exc}")
    if bootstrap.returncode != 0:
        fail(f"records fixture initialization failed: {bootstrap.stderr.strip()}")

    database = state_dir / "records.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "UPDATE records SET status = ? WHERE id = ?",
            ((status, record_id) for record_id, status in statuses.items()),
        )
        connection.execute("DELETE FROM operations")
        connection.execute("DELETE FROM notifications")
    return database


def verify_operations(database: Path, statuses: dict[str, str]) -> None:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    with connection:
        operations = connection.execute(
            "SELECT command, payload, result, started_ns, finished_ns "
            "FROM operations ORDER BY started_ns"
        ).fetchall()
        rows = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT id, name, region, status, cancelled "
                "FROM records ORDER BY rowid"
            ).fetchall()
        )
        notifications = connection.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]

    expected_rows = tuple(
        (record_id, name, region, statuses.get(record_id, status), cancelled)
        for record_id, name, region, status, cancelled in SEED_ROWS
    )
    if rows != expected_rows:
        fail("the audit modified the backing records")
    if notifications != 0:
        fail("the audit created a notification")
    if len(operations) != 3:
        fail(f"expected exactly two searches and one retrieval, got {len(operations)} operations")

    searches = [row for row in operations if row["command"] == "search"]
    retrieves = [row for row in operations if row["command"] == "retrieve"]
    if len(searches) != 2 or len(retrieves) != 1:
        fail("operations must consist of exactly two searches and one retrieval")

    discovered_ids: set[str] = set()
    observed_searches: set[tuple[str, str]] = set()
    for index, search in enumerate(searches, start=1):
        payload = load_json(search["payload"], f"search {index} payload")
        if not isinstance(payload, dict):
            fail(f"search {index} payload was not an object")
        key = (payload.get("name"), payload.get("region"))
        if key not in EXPECTED_SEARCHES:
            fail(f"unexpected search arguments: {payload!r}")
        result = load_json(search["result"], f"search {index} result")
        expected_result = {"matches": [{"id": EXPECTED_SEARCHES[key]}]}
        if result != expected_result:
            fail(f"search for {key[0]} did not return exactly one expected record")
        observed_searches.add(key)
        discovered_ids.add(EXPECTED_SEARCHES[key])

    if observed_searches != set(EXPECTED_SEARCHES):
        fail("the two required name-and-region searches were not both performed")
    if any(row["finished_ns"] is None for row in searches):
        fail("both searches must finish")
    if max(row["started_ns"] for row in searches) >= min(
        row["finished_ns"] for row in searches
    ):
        fail("the two independent searches did not overlap")

    retrieve = retrieves[0]
    if retrieve["started_ns"] < max(row["finished_ns"] for row in searches):
        fail("retrieval began before both searches finished")
    payload = load_json(retrieve["payload"], "retrieval payload")
    if not isinstance(payload, dict) or payload.get("ids") is None:
        fail("retrieval payload did not contain record IDs")
    ids = payload["ids"]
    if not isinstance(ids, list) or len(ids) != 2 or set(ids) != discovered_ids:
        fail("both discovered IDs must be retrieved together in one operation")
    result = load_json(retrieve["result"], "retrieval result")
    if not isinstance(result, dict) or not isinstance(result.get("records"), list):
        fail("retrieval did not return a records list")
    returned = {
        (record.get("name"), record.get("status"))
        for record in result["records"]
        if isinstance(record, dict)
    }
    expected_returned = {
        ("Juniper Bakery", statuses["rec-7f3a91"]),
        ("Northline Pediatrics", statuses["rec-2c8e44"]),
    }
    if returned != expected_returned:
        fail("retrieval did not return both discovered records and their statuses")


def expected_stdout(statuses: dict[str, str]) -> str:
    juniper_status = statuses["rec-7f3a91"]
    northline_status = statuses["rec-2c8e44"]
    comparison = "match" if juniper_status == northline_status else "differ"
    return (
        f"Juniper Bakery: {juniper_status}\n"
        f"Northline Pediatrics: {northline_status}\n"
        f"Comparison: statuses {comparison} — Juniper Bakery is {juniper_status}; "
        f"Northline Pediatrics is {northline_status}.\n"
    )


def main() -> None:
    if not SCRIPT.is_file():
        fail("run_status_audit.sh is missing")
    if not os.access(SCRIPT, os.X_OK):
        fail("run_status_audit.sh is not executable")
    if not RECORDS.is_file() or not os.access(RECORDS, os.X_OK):
        fail("the protected records fixture is not executable")

    source = SCRIPT.read_text(encoding="utf-8", errors="replace").lower()
    forbidden = (".records-state", "records.sqlite3", "sqlite3", "pi_records_state_dir")
    found = [token for token in forbidden if token in source]
    if found:
        fail(f"script accesses backing state directly: {', '.join(found)}")

    for statuses in STATUS_CASES:
        with tempfile.TemporaryDirectory(prefix="status-audit-") as temporary:
            state_dir = Path(temporary) / "state"
            env = os.environ.copy()
            env["PI_RECORDS_STATE_DIR"] = str(state_dir)
            database = prepare_state(state_dir, env, statuses)
            try:
                completed = subprocess.run(
                    [str(SCRIPT)],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                fail(f"could not execute run_status_audit.sh: {exc}")
            if completed.returncode != 0:
                fail(
                    "run_status_audit.sh exited unsuccessfully: "
                    f"{completed.stderr.strip()}"
                )
            expected = expected_stdout(statuses)
            if completed.stdout != expected:
                fail(
                    "standard output did not match the required three lines: "
                    f"{completed.stdout!r}"
                )
            verify_operations(database, statuses)

    print("PASS: status audit behavior verified")


if __name__ == "__main__":
    main()
