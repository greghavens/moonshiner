#!/usr/bin/env python3
"""Protected acceptance checks for the order availability orchestration."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "run_availability_check.py"
DATA = ROOT / "data" / "orders.json"
FAILURES = ROOT / "data" / "failures.json"

BOISE = ("Ergonomic chair order", "Boise", "2026-08-11")
PHOENIX = ("Volunteer appreciation kits", "Phoenix", "2026-08-11")


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_scenario(
    *,
    data_file: Path,
    failures_file: Path,
    state_db: Path,
    expected_lines: list[str],
    retried: tuple[str, str, str],
    preserved: tuple[str, str, str],
) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "ORDERDESK_DATA_FILE": str(data_file),
            "ORDERDESK_FAILURES_FILE": str(failures_file),
            "ORDERDESK_STATE_DB": str(state_db),
        }
    )
    completed = subprocess.run(
        [str(SCRIPT)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    require(completed.returncode == 0, f"script exited {completed.returncode}: {completed.stderr}")
    require(completed.stderr == "", "successful run must not write to standard error")
    actual_lines = completed.stdout.splitlines()
    require(actual_lines == expected_lines, f"unexpected report lines: {actual_lines!r}")

    connection = sqlite3.connect(state_db)
    connection.row_factory = sqlite3.Row
    calls = connection.execute(
        """
        SELECT call_id, operation, name, location, day, attempt,
               started_ns, finished_ns, outcome
        FROM audit ORDER BY call_id
        """
    ).fetchall()
    require(len(calls) == 3, f"expected exactly three availability calls, found {len(calls)}")
    require(
        all(row["operation"] == "availability" for row in calls),
        "a forbidden non-availability operation was invoked",
    )

    by_request: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
    for row in calls:
        key = (str(row["name"]), str(row["location"]), str(row["day"]))
        by_request.setdefault(key, []).append(row)
    require(set(by_request) == {BOISE, PHOENIX}, "availability arguments did not match both requested branches")
    require(len(by_request[retried]) == 2, "transiently failed branch was not called exactly twice")
    require(len(by_request[preserved]) == 1, "successful initial branch was repeated")

    retried_calls = sorted(by_request[retried], key=lambda row: int(row["attempt"]))
    require(
        [str(row["outcome"]) for row in retried_calls] == ["rate_limited", "success"],
        "retry branch did not preserve the injected transient-then-success sequence",
    )
    require(int(retried_calls[0]["attempt"]) == 1 and int(retried_calls[1]["attempt"]) == 2, "retry attempt numbers are incorrect")
    require(str(by_request[preserved][0]["outcome"]) == "success", "preserved branch did not succeed")

    first_attempts = [row for row in calls if int(row["attempt"]) == 1]
    require(len(first_attempts) == 2, "both initial calls were not made")
    latest_start = max(int(row["started_ns"]) for row in first_attempts)
    earliest_finish = min(int(row["finished_ns"]) for row in first_attempts)
    require(latest_start < earliest_finish, "initial availability processes did not overlap")
    last_initial_finish = max(int(row["finished_ns"]) for row in first_attempts)
    require(
        int(retried_calls[1]["started_ns"]) >= last_initial_finish,
        "retry began before both initial results were collected",
    )


def main() -> int:
    require(SCRIPT.is_file(), "run_availability_check.py is missing")
    mode = SCRIPT.stat().st_mode
    require(bool(mode & stat.S_IXUSR), "run_availability_check.py must be executable")
    original_hashes = {DATA: digest(DATA), FAILURES: digest(FAILURES)}

    with tempfile.TemporaryDirectory(prefix="orderdesk-verify-") as temporary:
        temp = Path(temporary)
        run_scenario(
            data_file=DATA,
            failures_file=FAILURES,
            state_db=temp / "primary.sqlite3",
            expected_lines=[
                "Ergonomic chair order — Boise: available",
                "Volunteer appreciation kits — Phoenix: unavailable",
                "Retried: Ergonomic chair order — Boise",
            ],
            retried=BOISE,
            preserved=PHOENIX,
        )

        alternate_data = temp / "orders.json"
        alternate_data.write_text(
            json.dumps(
                [
                    {
                        "id": "com-166",
                        "name": BOISE[0],
                        "location": BOISE[1],
                        "date": BOISE[2],
                        "status": "backordered",
                        "available": False,
                    },
                    {
                        "id": "com-566",
                        "name": PHOENIX[0],
                        "location": PHOENIX[1],
                        "date": PHOENIX[2],
                        "status": "processing",
                        "available": True,
                    },
                ]
            ),
            encoding="utf-8",
        )
        alternate_failures = temp / "failures.json"
        alternate_failures.write_text(
            json.dumps(
                [
                    {
                        "operation": "availability",
                        "name": PHOENIX[0],
                        "location": PHOENIX[1],
                        "date": PHOENIX[2],
                        "occurrence": 1,
                        "code": "rate_limited",
                        "transient": True,
                    }
                ]
            ),
            encoding="utf-8",
        )
        run_scenario(
            data_file=alternate_data,
            failures_file=alternate_failures,
            state_db=temp / "alternate.sqlite3",
            expected_lines=[
                "Ergonomic chair order — Boise: unavailable",
                "Volunteer appreciation kits — Phoenix: available",
                "Retried: Volunteer appreciation kits — Phoenix",
            ],
            retried=PHOENIX,
            preserved=BOISE,
        )

        nontransient_failures = temp / "nontransient-failures.json"
        nontransient_failures.write_text(
            json.dumps(
                [
                    {
                        "operation": "availability",
                        "name": PHOENIX[0],
                        "location": PHOENIX[1],
                        "date": PHOENIX[2],
                        "occurrence": 1,
                        "code": "permission_denied",
                        "transient": False,
                    }
                ]
            ),
            encoding="utf-8",
        )
        nontransient_state = temp / "nontransient.sqlite3"
        environment = os.environ.copy()
        environment.update(
            {
                "ORDERDESK_DATA_FILE": str(DATA),
                "ORDERDESK_FAILURES_FILE": str(nontransient_failures),
                "ORDERDESK_STATE_DB": str(nontransient_state),
            }
        )
        subprocess.run(
            [str(SCRIPT)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
        connection = sqlite3.connect(nontransient_state)
        connection.row_factory = sqlite3.Row
        calls = connection.execute(
            """
            SELECT operation, name, location, day, attempt,
                   started_ns, finished_ns, outcome
            FROM audit ORDER BY call_id
            """
        ).fetchall()
        require(len(calls) == 2, "a non-transient failure must not be retried")
        require(
            all(row["operation"] == "availability" for row in calls),
            "a forbidden non-availability operation was invoked",
        )
        require(
            {
                (str(row["name"]), str(row["location"]), str(row["day"]))
                for row in calls
            }
            == {BOISE, PHOENIX},
            "non-transient scenario did not make both requested initial calls",
        )
        require(
            all(int(row["attempt"]) == 1 for row in calls),
            "a non-transient failure was retried",
        )
        require(
            {str(row["outcome"]) for row in calls}
            == {"success", "permission_denied"},
            "non-transient scenario produced unexpected outcomes",
        )
        latest_start = max(int(row["started_ns"]) for row in calls)
        earliest_finish = min(int(row["finished_ns"]) for row in calls)
        require(
            latest_start < earliest_finish,
            "non-transient initial availability processes did not overlap",
        )
        connection.close()

    for path, expected_hash in original_hashes.items():
        require(digest(path) == expected_hash, f"protected state changed: {path.relative_to(ROOT)}")
    print("verification passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except VerificationError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        sys.exit(1)
