#!/usr/bin/env python3
"""Protected acceptance checks for behavior-dependency-planning-0007."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "stockroom_audit.py"
BASE_DATABASE = ROOT / "data" / "inventory.db"
TARGETS = (
    ("Reinforced Packing Tape", "Warehouse C"),
    ("Recycled Printer Paper", "Warehouse D"),
)


class VerificationError(AssertionError):
    pass


@dataclass(frozen=True)
class Scenario:
    name: str
    sql: tuple[str, ...] = ()


SCENARIOS = (
    Scenario("nominal"),
    Scenario(
        "missing-first",
        ("DELETE FROM inventory WHERE stable_id = 'STK-7F3A91'",),
    ),
    Scenario(
        "missing-second",
        ("DELETE FROM inventory WHERE stable_id = 'STK-4C8D22'",),
    ),
    Scenario(
        "ambiguous-first",
        (
            """INSERT INTO inventory VALUES (
                   'STK-0A0A02', 'Reinforced Packing Tape', 'Warehouse C',
                   '2026-07-17', 'Backordered', 'C-44-2', 20,
                   'Secondary intake record awaiting reconciliation'
               )""",
        ),
    ),
    Scenario(
        "ambiguous-second",
        (
            """INSERT INTO inventory VALUES (
                   'STK-0A0A03', 'Recycled Printer Paper', 'Warehouse D',
                   '2026-07-18', 'Low', 'D-08-2', 36,
                   'Duplicate receiving record pending review'
               )""",
        ),
    ),
    Scenario(
        "both-missing",
        (
            "DELETE FROM inventory WHERE stable_id = 'STK-7F3A91'",
            "DELETE FROM inventory WHERE stable_id = 'STK-4C8D22'",
        ),
    ),
    Scenario(
        "same-status",
        ("UPDATE inventory SET status = 'Low' WHERE stable_id = 'STK-4C8D22'",),
    ),
)


def fail(message: str) -> None:
    raise VerificationError(message)


def validate_source() -> None:
    if not CANDIDATE.is_file():
        fail("stockroom_audit.py is missing")
    source = CANDIDATE.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(CANDIDATE))
    except SyntaxError as error:
        fail(f"stockroom_audit.py is not valid Python: {error}")

    forbidden_imports = {"sqlite3", "duckdb", "pandas"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    blocked = sorted(imported & forbidden_imports)
    if blocked:
        fail("direct database access is forbidden: " + ", ".join(blocked))
    nonstandard = sorted(imported - sys.stdlib_module_names)
    if nonstandard:
        fail("only Python's standard library may be used: " + ", ".join(nonstandard))

    docstrings: set[int] = set()
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if (
            scope.body
            and isinstance(scope.body[0], ast.Expr)
            and isinstance(scope.body[0].value, ast.Constant)
            and isinstance(scope.body[0].value.value, str)
        ):
            docstrings.add(id(scope.body[0].value))

    runtime_strings = " ".join(
        node.value.casefold().replace("\n", " ")
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )
    forbidden_runtime_fragments = (
        "inventory.db",
        "inventory_database",
        "data/",
        ".moonshiner",
        "sqlite3",
        "pragma ",
        "select ",
        " list ",
        "preferences",
        "availability",
        " create ",
        " update ",
        " cancel ",
        " notify ",
    )
    padded = " " + runtime_strings + " "
    for fragment in forbidden_runtime_fragments:
        if fragment in padded:
            fail(f"candidate crosses a forbidden scope boundary: {fragment.strip()}")


def validate_scope() -> None:
    if not (ROOT / ".git").exists():
        return
    commands = (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    changed: set[str] = set()
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            fail("could not inspect candidate file scope")
        changed.update(line for line in completed.stdout.splitlines() if line)
    expected = {"stockroom_audit.py"} if CANDIDATE.exists() else set()
    if changed != expected:
        fail(
            "candidate must change exactly stockroom_audit.py; observed: "
            + ", ".join(sorted(changed))
        )


def prepare_database(destination: Path, statements: tuple[str, ...]) -> None:
    shutil.copy2(BASE_DATABASE, destination)
    if not statements:
        return
    with sqlite3.connect(destination) as connection:
        for statement in statements:
            connection.execute(statement)
        connection.commit()


def expected_records(database: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    with sqlite3.connect(database) as connection:
        for name, location in TARGETS:
            matches = connection.execute(
                """
                SELECT stable_id, name, location, record_date, status
                  FROM inventory
                 WHERE name = ? AND location = ?
                 ORDER BY stable_id
                """,
                (name, location),
            ).fetchall()
            if len(matches) == 1:
                stable_id, record_name, record_location, date, status = matches[0]
                records.append(
                    {
                        "stable_id": stable_id,
                        "name": record_name,
                        "location": record_location,
                        "date": date,
                        "status": status,
                    }
                )
    return records


def expected_output(records: list[dict[str, str]]) -> str:
    lines = [
        "Name: {name} | Stable ID: {stable_id} | Location: {location} | "
        "Date: {date} | Status: {status}".format(**record)
        for record in records
    ]
    if len(records) < 2:
        lines.append(
            "Status comparison: Unavailable because fewer than two records resolved."
        )
    elif records[0]["status"] == records[1]["status"]:
        lines.append(
            f'Status comparison: Both records have status "{records[0]["status"]}".'
        )
    else:
        lines.append(
            f'Status comparison: "{records[0]["name"]}" is "{records[0]["status"]}", '
            f'while "{records[1]["name"]}" is "{records[1]["status"]}".'
        )
    return "\n".join(lines) + "\n"


def load_events(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        fail("inventory executable was not invoked")
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (json.JSONDecodeError, OSError) as error:
        fail(f"inventory audit log is invalid: {error}")


def overlaps(first: dict[str, object], second: dict[str, object]) -> bool:
    return int(first["started_ns"]) < int(second["finished_ns"]) and int(
        second["started_ns"]
    ) < int(first["finished_ns"])


def validate_events(events: list[dict[str, object]], records: list[dict[str, str]]) -> None:
    if any(event.get("ok") is not True for event in events):
        fail("an inventory operation failed")
    searches = [event for event in events if event.get("command") == "search"]
    gets = [event for event in events if event.get("command") == "get"]
    if len(searches) != 2 or len(gets) != len(records) or len(events) != 2 + len(records):
        fail("unexpected inventory operation count or operation type")

    observed_targets = {(event.get("name"), event.get("location")) for event in searches}
    if observed_targets != set(TARGETS):
        fail("the two exact name-and-location searches were not issued")
    if not overlaps(searches[0], searches[1]):
        fail("the two searches did not overlap in time")

    if gets:
        last_search_finish = max(int(event["finished_ns"]) for event in searches)
        first_get_start = min(int(event["started_ns"]) for event in gets)
        if first_get_start < last_search_finish:
            fail("a retrieval started before both searches completed")

    expected_ids = {record["stable_id"] for record in records}
    observed_ids = {event.get("stable_id") for event in gets}
    if observed_ids != expected_ids:
        fail("retrieval branches did not match exactly-one search resolution")
    if len(gets) == 2 and not overlaps(gets[0], gets[1]):
        fail("the two retrievals did not overlap in time")


def run_scenario(scenario: Scenario, directory: Path) -> None:
    database = directory / f"{scenario.name}.db"
    audit_log = directory / f"{scenario.name}.jsonl"
    prepare_database(database, scenario.sql)
    records = expected_records(database)

    environment = os.environ.copy()
    environment["INVENTORY_DATABASE"] = str(database)
    environment["INVENTORY_AUDIT_LOG"] = str(audit_log)
    environment["INVENTORY_OPERATION_DELAY_MS"] = "0"
    environment["INVENTORY_TEST_BARRIER"] = str(directory / f"{scenario.name}-barrier")
    environment["INVENTORY_TEST_SEARCH_PEERS"] = "2"
    environment["INVENTORY_TEST_GET_PEERS"] = str(len(records))
    completed = subprocess.run(
        [sys.executable, str(CANDIDATE)],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=8,
        check=False,
    )
    if completed.returncode != 0:
        fail(
            f"{scenario.name}: program exited {completed.returncode}; "
            f"stderr was {completed.stderr!r}"
        )
    if completed.stderr:
        fail(f"{scenario.name}: program wrote diagnostics to stderr")
    wanted = expected_output(records)
    if completed.stdout != wanted:
        fail(
            f"{scenario.name}: report mismatch\n"
            f"expected: {wanted!r}\n"
            f"actual:   {completed.stdout!r}"
        )
    validate_events(load_events(audit_log), records)


def main() -> int:
    try:
        validate_scope()
        validate_source()
        with tempfile.TemporaryDirectory(prefix="inventory-acceptance-") as temporary:
            directory = Path(temporary)
            for scenario in SCENARIOS:
                run_scenario(scenario, directory)
    except (VerificationError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: stockroom audit dependency plan verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
