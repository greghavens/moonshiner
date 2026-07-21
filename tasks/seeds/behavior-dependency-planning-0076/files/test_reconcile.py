#!/usr/bin/env python3
"""Protected deterministic acceptance checks for reconcile_claims.py."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
PROGRAM = ROOT / "reconcile_claims.py"
SCRATCH = ROOT / ".claim_verify"
TARGETS = (
    ("Clinic Refrigeration Claim", "Medical Desk"),
    ("Museum Glass Damage Claim", "Arts Desk"),
)
FIELDS = ("id", "name", "location", "status", "date")
FORBIDDEN_OPS = {
    "list", "profile", "availability", "create", "update", "cancel", "notify"
}


SCENARIOS = {
    "both_unique": [
        ("ins-176", *TARGETS[0], "under-review", "2026-10-04"),
        ("ins-576", *TARGETS[1], "coverage-review", "2026-10-05"),
        ("ins-976", "Clinic Refrigeration Claim", "Medical Desk Closed",
         "closed", "2025-10-04"),
        ("ins-876", "Museum Glass Damage Claim", "Arts Desk Closed Files",
         "closed", "2025-10-05"),
    ],
    "reverse_date_order": [
        ("queue-a41", *TARGETS[0], "documentation-review", "2027-03-18"),
        ("queue-b09", *TARGETS[1], "field-review", "2027-03-02"),
        ("queue-decoy", "Museum Glass Damage Claim", "Arts Annex",
         "archived", "2020-01-01"),
    ],
    "equal_date_request_order": [
        ("tie-clinic", *TARGETS[0], "engineering-review", "2027-06-12"),
        ("tie-museum", *TARGETS[1], "valuation-review", "2027-06-12"),
    ],
    "clinic_ambiguous": [
        ("fork-left", *TARGETS[0], "review-a", "2028-01-01"),
        ("fork-right", *TARGETS[0], "review-b", "2028-01-02"),
        ("single-art", *TARGETS[1], "specialist-review", "2028-02-01"),
    ],
    "clinic_missing": [
        ("wrong-desk", "Clinic Refrigeration Claim", "North Medical Desk",
         "triage", "2029-05-01"),
        ("arts-only", *TARGETS[1], "awaiting-documents", "2029-05-03"),
    ],
    "museum_missing": [
        ("clinic-only", *TARGETS[0], "awaiting-inspection", "2029-06-04"),
        ("wrong-arts", "Museum Glass Damage Claim", "Arts Annex",
         "triage", "2029-06-05"),
    ],
    "both_missing": [
        ("clinic-decoy", "Clinic Refrigeration Claim", "Medical Annex",
         "archived", "2030-01-01"),
        ("museum-decoy", "Museum Glass Damage Claim", "Museum Desk",
         "archived", "2030-01-02"),
    ],
}


class Failure(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def write_records(path: Path, rows: list[tuple[str, str, str, str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(FIELDS)
        writer.writerows(rows)


def read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line]


def expected_report(rows: list[tuple[str, str, str, str, str]]) -> str:
    records = [dict(zip(FIELDS, row, strict=True)) for row in rows]
    resolved: list[tuple[int, dict[str, str]]] = []
    unresolved: list[tuple[str, str]] = []
    for index, (name, location) in enumerate(TARGETS):
        matches = [record for record in records
                   if record["name"] == name and record["location"] == location]
        if len(matches) == 1:
            resolved.append((index, matches[0]))
        else:
            unresolved.append((name, location))
    resolved.sort(key=lambda item: (item[1]["date"], item[0]))
    lines = ["\t".join(record[field] for field in ("date", "id", "name",
                                                     "location", "status"))
             for _, record in resolved]
    lines.extend(f"UNRESOLVED\t{name}\t{location}"
                 for name, location in unresolved)
    if not unresolved:
        earlier, later = (item[1] for item in resolved)
        lines.append(f"Status comparison: {earlier['name']} is {earlier['status']}; "
                     f"{later['name']} is {later['status']}.")
    else:
        names = " and ".join(name for name, _ in unresolved)
        lines.append(f"Status comparison: unavailable because {names} "
                     "did not resolve uniquely.")
    return "\n".join(lines) + "\n"


def one_event(events: list[dict[str, object]], op: str, phase: str,
              pid: int) -> dict[str, object]:
    matches = [event for event in events
               if event.get("op") == op and event.get("phase") == phase
               and event.get("pid") == pid]
    check(len(matches) == 1,
          f"expected one {op}/{phase} event for process {pid}, got {len(matches)}")
    return matches[0]


def audit_trace(events: list[dict[str, object]],
                rows: list[tuple[str, str, str, str, str]]) -> None:
    check(events, "claims command audit is empty; execute claims_cli.py")
    check(all(event.get("entrypoint") == "__main__" for event in events),
          "claims_cli.py must be executed as a command, not imported")
    operations = {str(event.get("op")) for event in events}
    check(not (operations & FORBIDDEN_OPS),
          f"forbidden claims operation(s): {sorted(operations & FORBIDDEN_OPS)}")
    check(operations <= {"search", "get"},
          f"unexpected claims operation(s): {sorted(operations - {'search', 'get'})}")
    errors = [event for event in events if event.get("phase") == "error"]
    check(not errors, f"claims command recorded errors: {errors}")

    search_starts = [event for event in events
                     if event.get("op") == "search" and event.get("phase") == "start"]
    search_ends = [event for event in events
                   if event.get("op") == "search" and event.get("phase") == "end"]
    check(len(search_starts) == 2 and len(search_ends) == 2,
          "issue exactly two searches and complete both")
    actual_targets = [(event.get("name"), event.get("location"))
                      for event in search_starts]
    check(sorted(actual_targets) == sorted(TARGETS),
          f"searches did not use both exact name/location pairs: {actual_targets}")
    search_pids = {int(event["pid"]) for event in search_starts}
    check(len(search_pids) == 2, "the searches must be separate concurrent processes")
    for pid in search_pids:
        one_event(events, "search", "end", pid)
    check(max(int(event["time_ns"]) for event in search_starts)
          < min(int(event["time_ns"]) for event in search_ends),
          "both searches must start before either search finishes")

    records = [dict(zip(FIELDS, row, strict=True)) for row in rows]
    eligible: dict[str, tuple[str, str]] = {}
    for name, location in TARGETS:
        matches = [record["id"] for record in records
                   if record["name"] == name and record["location"] == location]
        if len(matches) == 1 and matches[0]:
            eligible[matches[0]] = (name, location)

    get_starts = [event for event in events
                  if event.get("op") == "get" and event.get("phase") == "start"]
    get_ends = [event for event in events
                if event.get("op") == "get" and event.get("phase") == "end"]
    check(len(get_starts) == len(eligible) and len(get_ends) == len(eligible),
          "retrieve every and only uniquely resolved branch")
    actual_get_ids = [str(event.get("id")) for event in get_starts]
    check(sorted(actual_get_ids) == sorted(eligible),
          f"get IDs lack search lineage: {actual_get_ids}")
    check(not get_starts or
          min(int(event["time_ns"]) for event in get_starts)
          > max(int(event["time_ns"]) for event in search_ends),
          "no get may start until both searches have finished")
    get_pids = {int(event["pid"]) for event in get_starts}
    check(len(get_pids) == len(get_starts), "each get must be a separate process")
    for pid in get_pids:
        one_event(events, "get", "end", pid)
    if len(get_starts) == 2:
        check(max(int(event["time_ns"]) for event in get_starts)
              < min(int(event["time_ns"]) for event in get_ends),
              "both eligible gets must start before either get finishes")


def static_shortcut_checks() -> None:
    source = PROGRAM.read_text(encoding="utf-8")
    forbidden_fragments = (
        "CLAIMS_RECORDS", "CLAIMS_AUDIT", "test_reconcile", ".tsv",
        "ins-176", "ins-576", "queue-a41", "under-review",
        "coverage-review", "single-art", "arts-only",
        "import claims_cli", "from claims_cli", "runpy", "importlib",
    )
    found = [fragment for fragment in forbidden_fragments if fragment in source]
    check(not found, f"implementation contains fixture/data shortcuts: {found}")
    check("claims_cli" in source,
          "implementation must execute the provided claims command")


def run_scenario(name: str,
                 rows: list[tuple[str, str, str, str, str]]) -> None:
    directory = SCRATCH / name
    directory.mkdir(parents=True)
    records = directory / "records.tsv"
    audit = directory / "audit.jsonl"
    write_records(records, rows)
    before = records.read_bytes()
    env = {
        **os.environ,
        "CLAIMS_RECORDS": str(records),
        "CLAIMS_AUDIT": str(audit),
        "CLAIMS_RUN_ID": name,
        "CLAIMS_PREFERENCES": str(directory / "preferences.json"),
        "CLAIMS_NOTIFICATIONS": str(directory / "notifications.jsonl"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run([sys.executable, str(PROGRAM)], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=12)
    check(result.returncode == 0,
          f"{name}: program exited {result.returncode}; stderr={result.stderr!r}")
    check(result.stderr == "", f"{name}: unexpected stderr: {result.stderr!r}")
    check(result.stdout == expected_report(rows),
          f"{name}: report mismatch\n--- expected ---\n{expected_report(rows)}"
          f"--- actual ---\n{result.stdout}")
    check(records.read_bytes() == before, f"{name}: record store was modified")
    check(not (directory / "notifications.jsonl").exists(),
          f"{name}: notification side effect was created")
    check(not (directory / "preferences.json").exists(),
          f"{name}: preferences should not be created or read")
    audit_trace(read_events(audit), rows)


def run_failure_scenario(
        name: str,
        rows: list[tuple[str, str, str, str, str]] | None,
        failure_stage: str) -> None:
    directory = SCRATCH / name
    directory.mkdir(parents=True)
    records = directory / "records.tsv"
    audit = directory / "audit.jsonl"
    if rows is None:
        records.write_text("invalid\theader\nvalue\tvalue\n", encoding="utf-8")
    else:
        write_records(records, rows)
    before = records.read_bytes()
    env = {
        **os.environ,
        "CLAIMS_RECORDS": str(records),
        "CLAIMS_AUDIT": str(audit),
        "CLAIMS_RUN_ID": name,
        "CLAIMS_PREFERENCES": str(directory / "preferences.json"),
        "CLAIMS_NOTIFICATIONS": str(directory / "notifications.jsonl"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run([sys.executable, str(PROGRAM)], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=12)
    check(result.returncode != 0, f"{name}: malformed audit unexpectedly passed")
    check(result.stdout == "", f"{name}: failure wrote stdout: {result.stdout!r}")
    check(bool(result.stderr.strip()), f"{name}: failure lacked a stderr diagnostic")
    check(records.read_bytes() == before, f"{name}: record store was modified")
    check(not (directory / "notifications.jsonl").exists(),
          f"{name}: notification side effect was created")
    check(not (directory / "preferences.json").exists(),
          f"{name}: preferences should not be created or read")

    events = read_events(audit)
    check(events, f"{name}: claims command audit is empty")
    check(all(event.get("entrypoint") == "__main__" for event in events),
          f"{name}: claims_cli.py was imported instead of executed")
    operations = {str(event.get("op")) for event in events}
    check(not (operations & FORBIDDEN_OPS),
          f"{name}: forbidden claims operation(s): {operations & FORBIDDEN_OPS}")
    search_starts = [event for event in events
                     if event.get("op") == "search" and event.get("phase") == "start"]
    search_ends = [event for event in events
                   if event.get("op") == "search" and event.get("phase") == "end"]
    search_errors = [event for event in events
                     if event.get("op") == "search" and event.get("phase") == "error"]
    check(len(search_starts) == 2, f"{name}: both searches were not launched")
    check(len({event.get("pid") for event in search_starts}) == 2,
          f"{name}: searches were not separate processes")

    if failure_stage == "command":
        check(len(search_errors) == 2 and not search_ends,
              f"{name}: both failed commands were not awaited: {events}")
        check(operations == {"search"}, f"{name}: get ran after a search failure")
    elif failure_stage == "search_response":
        check(len(search_ends) == 2 and not search_errors,
              f"{name}: searches did not complete successfully: {events}")
        check(operations == {"search"},
              f"{name}: get ran after a malformed search response")
    elif failure_stage == "get_response":
        check(rows is not None, f"{name}: missing rows for get audit")
        audit_trace(events, rows)
    else:  # pragma: no cover - test author owns the stage set
        raise Failure(f"unknown failure stage: {failure_stage}")


def main() -> int:
    if not PROGRAM.is_file():
        print("FAIL reconcile_claims.py is missing")
        return 1
    shutil.rmtree(SCRATCH, ignore_errors=True)
    try:
        static_shortcut_checks()
        for name, rows in SCENARIOS.items():
            run_scenario(name, rows)
        run_failure_scenario("command_failure", None, "command")
        run_failure_scenario("malformed_search_response", [
            ("", *TARGETS[0], "review", "2031-02-01"),
            ("valid-art", *TARGETS[1], "review", "2031-02-02"),
        ], "search_response")
        run_failure_scenario("malformed_get_response", [
            ("valid-clinic", *TARGETS[0], "", "2031-03-01"),
            ("valid-museum", *TARGETS[1], "review", "2031-03-02"),
        ], "get_response")
    except (Failure, OSError, subprocess.SubprocessError,
            json.JSONDecodeError) as error:
        print(f"FAIL {error}")
        return 1
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"PASS {len(SCENARIOS)} reports and 3 audit-failure scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
