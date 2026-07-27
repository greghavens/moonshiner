#!/usr/bin/env python3
"""Protected deterministic verifier for the CRM dependency workflow."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / ".harness"
RUNTIME = PRIVATE / "runtime"

ALDER = {
    "id": "acct-west-af-104",
    "name": "Alder & Finch Books",
    "region": "West Region",
    "status": "customer",
    "owner": "Morgan Lee",
}
SUNRISE = {
    "id": "acct-central-sf-307",
    "name": "Sunrise Food Pantry",
    "region": "Central Region",
    "status": "prospect",
    "owner": "Riley Chen",
}
OTHER_RECORDS = [
    {
        "id": "acct-central-af-219",
        "name": "Alder & Finch Books",
        "region": "Central Region",
        "status": "prospect",
        "owner": "Avery Patel",
    },
    {
        "id": "acct-west-sf-411",
        "name": "Sunrise Food Pantry",
        "region": "West Region",
        "status": "qualified",
        "owner": "Jordan Bell",
    },
    {
        "id": "acct-central-sfa-512",
        "name": "Sunrise Food Pantry Annex",
        "region": "Central Region",
        "status": "prospect",
        "owner": "Casey Woods",
    },
    {
        "id": "acct-west-af-archive-033",
        "name": "Alder & Finch Books Archive",
        "region": "West Region",
        "status": "inactive",
        "owner": "Morgan Lee",
    },
]
EXPECTED_REPORT = """- Alder & Finch Books | West Region | customer
- Sunrise Food Pantry | Central Region | prospect
- Action | changed Sunrise Food Pantry from prospect to qualified; notified account team after the change succeeded
"""


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{path.name} is unreadable: {exc}") from exc
    assert isinstance(value, dict), f"{path.name} must contain an object"
    return value


def records_state(
    status: str,
    *,
    fail_qualify: bool = False,
    fail_notify: bool = False,
    alder_id: str = "acct-west-af-104",
    sunrise_id: str = "acct-central-sf-307",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "version": 1,
        "records": [
            {**ALDER, "id": alder_id},
            dict(OTHER_RECORDS[0]),
            {**SUNRISE, "id": sunrise_id, "status": status},
            *(dict(row) for row in OTHER_RECORDS[1:]),
        ],
    }
    faults: dict[str, bool] = {}
    if fail_qualify:
        faults["qualify"] = True
    if fail_notify:
        faults["notify"] = True
    if faults:
        value["faults"] = faults
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def assert_overlap(rows: list[dict[str, Any]], label: str) -> None:
    assert len(rows) == 2, f"{label} must contain exactly two operations"
    assert len({row.get("pid") for row in rows}) == 2, (
        f"{label} must use two distinct processes"
    )
    for row in rows:
        assert isinstance(row.get("started_ns"), int), f"{label} lacks start evidence"
        assert isinstance(row.get("finished_ns"), int), f"{label} lacks finish evidence"
        assert row["started_ns"] <= row["finished_ns"], f"{label} timing is invalid"
    assert max(row["started_ns"] for row in rows) < min(
        row["finished_ns"] for row in rows
    ), f"{label} operations did not overlap"


def assert_help_preceded_workflow(runtime: Path) -> None:
    history = read_object(runtime / "help.json")
    assert history.get("version") == 1, "unexpected help-history version"
    calls = history.get("calls")
    assert isinstance(calls, list) and calls, "crmctl help was not read"
    for call in calls:
        assert isinstance(call.get("started_ns"), int), "help read lacks start evidence"
        assert isinstance(call.get("finished_ns"), int), "help read lacks finish evidence"
        assert call["started_ns"] <= call["finished_ns"], "help-read timing is invalid"

    audit = read_object(runtime / "audit.json")
    operations = audit.get("operations")
    assert isinstance(operations, list) and operations, "CRM workflow is missing"
    assert min(call["finished_ns"] for call in calls) <= min(
        row.get("started_ns", -1) for row in operations
    ), "crmctl help was not read before CRM work began"


def assert_read_stages(
    log: list[dict[str, Any]],
    sunrise_status: str,
    *,
    alder_id: str = "acct-west-af-104",
    sunrise_id: str = "acct-central-sf-307",
) -> None:
    searches = log[:2]
    assert all(row.get("operation") == "search" for row in searches), (
        "the first dependency stage must contain only searches"
    )
    assert all(row.get("kind") == "read" and row.get("outcome") == "ok" for row in searches)
    observed = {
        (row.get("name"), row.get("region"), tuple(row.get("result_ids", [])))
        for row in searches
    }
    assert observed == {
        ("Alder & Finch Books", "West Region", (alder_id,)),
        ("Sunrise Food Pantry", "Central Region", (sunrise_id,)),
    }, "the scoped searches did not resolve the two unique protected records"
    assert_overlap(searches, "search stage")

    gets = log[2:4]
    assert all(row.get("operation") == "get" for row in gets), (
        "the second dependency stage must contain only complete-record retrievals"
    )
    assert all(row.get("kind") == "read" and row.get("outcome") == "ok" for row in gets)
    assert {row.get("record_id") for row in gets} == {
        alder_id,
        sunrise_id,
    }, "the retrieval stage did not use both discovered IDs"
    snapshots = {row["record_id"]: row.get("record") for row in gets}
    assert snapshots == {
        alder_id: {**ALDER, "id": alder_id},
        sunrise_id: {**SUNRISE, "id": sunrise_id, "status": sunrise_status},
    }, "complete-record evidence does not justify the conditional decision"
    assert_overlap(gets, "retrieval stage")
    assert max(row["finished_ns"] for row in searches) <= min(
        row["started_ns"] for row in gets
    ), "a retrieval began before both searches completed"


def assert_workflow(
    runtime: Path,
    *,
    sunrise_before: str,
    mode: str,
    fail_qualify: bool = False,
    fail_notify: bool = False,
    alder_id: str = "acct-west-af-104",
    sunrise_id: str = "acct-central-sf-307",
) -> None:
    state = read_object(runtime / "records.json")
    final_status = "qualified" if mode in {"changed", "notify_failed"} else sunrise_before
    assert state == records_state(
        final_status,
        fail_qualify=fail_qualify,
        fail_notify=fail_notify,
        alder_id=alder_id,
        sunrise_id=sunrise_id,
    ), (
        "the target transition is missing or a non-target account changed"
    )

    history = read_object(runtime / "audit.json")
    assert history.get("version") == 1, "unexpected audit version"
    log = history.get("operations")
    assert isinstance(log, list), "operation history must be a list"
    expected_count = {"unchanged": 4, "failed": 5, "changed": 6, "notify_failed": 6}[mode]
    assert len(log) == expected_count, (
        f"the workflow must contain exactly {expected_count} CRM operations"
    )
    assert [row.get("sequence") for row in log] == list(range(1, expected_count + 1)), (
        "operation sequence is not contiguous"
    )
    assert_read_stages(
        log,
        sunrise_before,
        alder_id=alder_id,
        sunrise_id=sunrise_id,
    )

    notices = read_object(runtime / "notices.json")
    assert notices.get("version") == 1 and isinstance(notices.get("notices"), list), (
        "notice state is malformed"
    )

    if mode == "unchanged":
        assert notices["notices"] == [], "a notice was sent when mutation was skipped"
        return

    update = log[4]
    assert update.get("operation") == "qualify", "the dependent write is not qualification"
    assert update.get("kind") == "write", "the qualification is not recorded as a write"
    assert update.get("record_id") == sunrise_id, "the wrong account was changed"
    assert update.get("previous_status") == "prospect", (
        "qualification was not conditioned on the retrieved prospect status"
    )
    assert update.get("new_status") == "qualified", "the target status is not qualified"
    assert max(row["finished_ns"] for row in log[2:4]) <= update.get("started_ns", -1), (
        "qualification began before both complete records were retrieved"
    )

    if mode == "failed":
        assert update.get("outcome") == "error" and update.get("changed") is False, (
            "the injected qualification failure was not preserved"
        )
        assert "receipt" not in update, "a failed mutation produced a success receipt"
        assert notices["notices"] == [], "a notice was sent after mutation failure"
        return

    assert update.get("outcome") == "ok" and update.get("changed") is True, (
        "the permitted qualification did not succeed exactly once"
    )
    receipt = update.get("receipt")
    assert isinstance(receipt, str) and receipt, "successful qualification lacks a receipt"

    notification = log[5]
    assert notification.get("operation") == "notify", "notification did not follow the change"
    assert notification.get("kind") == "notification"
    assert notification.get("audience") == "account team"
    assert notification.get("record_id") == sunrise_id
    assert notification.get("receipt") == receipt, (
        "notification is not gated by the successful mutation receipt"
    )
    assert update.get("finished_ns", sys.maxsize) <= notification.get("started_ns", -1), (
        "notification began before the mutation succeeded"
    )
    if mode == "notify_failed":
        assert notification.get("outcome") == "error", (
            "the injected notification failure was not preserved"
        )
        assert notices["notices"] == [], "a failed notification was recorded as sent"
        return

    assert notification.get("outcome") == "ok"
    assert notices["notices"] == [
        {
            "audience": "account team",
            "record_id": sunrise_id,
            "message": "Sunrise Food Pantry changed from prospect to qualified.",
            "receipt": receipt,
        }
    ], "the account team did not receive exactly the permitted outcome notice"


def assert_program_behavior(script: Path) -> None:
    scenarios = (
        ("prospect", "changed", False, False, 0, "acct-west-af-104", "acct-central-sf-307"),
        ("qualified", "unchanged", False, False, 0, "acct-west-af-104", "acct-central-sf-307"),
        ("prospect", "failed", True, False, None, "acct-west-af-104", "acct-central-sf-307"),
        ("prospect", "notify_failed", False, True, None, "acct-west-af-104", "acct-central-sf-307"),
        ("prospect", "changed", False, False, 0, "acct-review-af-901", "acct-review-sf-902"),
    )
    for (
        sunrise_status,
        mode,
        fail_qualify,
        fail_notify,
        expected_code,
        alder_id,
        sunrise_id,
    ) in scenarios:
        with tempfile.TemporaryDirectory(prefix="crm-verify-") as temporary:
            scenario = Path(temporary)
            private = scenario / ".harness"
            runtime = private / "runtime"
            runtime.mkdir(parents=True)
            shutil.copy2(ROOT / "crmctl", scenario / "crmctl")
            shutil.copy2(script, scenario / script.name)
            write_object(
                runtime / "records.json",
                records_state(
                    sunrise_status,
                    fail_qualify=fail_qualify,
                    fail_notify=fail_notify,
                    alder_id=alder_id,
                    sunrise_id=sunrise_id,
                ),
            )
            write_object(runtime / "audit.json", {"version": 1, "operations": []})
            write_object(runtime / "notices.json", {"version": 1, "notices": []})
            (private / "lock").write_text("", encoding="utf-8")

            try:
                completed = subprocess.run(
                    [f"./{script.name}"],
                    cwd=scenario,
                    text=True,
                    capture_output=True,
                    timeout=12,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise AssertionError(f"the program could not run in {mode} mode: {exc}") from exc
            if expected_code is not None:
                assert completed.returncode == expected_code, (
                    f"the program returned {completed.returncode}, not {expected_code}, "
                    f"in {mode} mode: {completed.stderr.strip()}"
                )

            assert_workflow(
                runtime,
                sunrise_before=sunrise_status,
                mode=mode,
                fail_qualify=fail_qualify,
                fail_notify=fail_notify,
                alder_id=alder_id,
                sunrise_id=sunrise_id,
            )
            report = scenario / "reconciliation.md"
            if mode in {"failed", "notify_failed"}:
                assert not report.exists(), "a failed mutation produced a success report"
            else:
                text = report.read_text(encoding="utf-8")
                if mode == "changed":
                    assert text == EXPECTED_REPORT, "the success report is incorrect"
                else:
                    assert text == (
                        "- Alder & Finch Books | West Region | customer\n"
                        "- Sunrise Food Pantry | Central Region | qualified\n"
                        "- Action | left Sunrise Food Pantry unchanged because its retrieved "
                        "status was qualified; no notification sent\n"
                    ), "the skipped-change report is incorrect"

            expected_names = {".harness", "crmctl", script.name}
            if mode not in {"failed", "notify_failed"}:
                expected_names.add("reconciliation.md")
            assert {path.name for path in scenario.iterdir()} == expected_names, (
                "the program left temporary working files behind"
            )

    lookup_cases = (
        ("missing", "Sunrise Food Pantry", "Central Region"),
        ("ambiguous", "Alder & Finch Books", "West Region"),
    )
    for case, expected_name, expected_region in lookup_cases:
        with tempfile.TemporaryDirectory(prefix="crm-verify-lookup-") as temporary:
            scenario = Path(temporary)
            private = scenario / ".harness"
            runtime = private / "runtime"
            runtime.mkdir(parents=True)
            shutil.copy2(ROOT / "crmctl", scenario / "crmctl")
            shutil.copy2(script, scenario / script.name)

            initial = records_state("prospect")
            records = initial["records"]
            if case == "missing":
                initial["records"] = [
                    row
                    for row in records
                    if not (
                        row.get("name") == expected_name
                        and row.get("region") == expected_region
                    )
                ]
            else:
                records.append({**ALDER, "id": "acct-ambiguous-af-903"})
            write_object(runtime / "records.json", initial)
            write_object(runtime / "audit.json", {"version": 1, "operations": []})
            write_object(runtime / "notices.json", {"version": 1, "notices": []})
            (private / "lock").write_text("", encoding="utf-8")

            completed = subprocess.run(
                [f"./{script.name}"],
                cwd=scenario,
                text=True,
                capture_output=True,
                timeout=12,
                check=False,
            )
            log = read_object(runtime / "audit.json").get("operations")
            assert isinstance(log, list) and len(log) == 2, (
                f"the {case} lookup did not stop before every retrieval"
            )
            assert all(row.get("operation") == "search" for row in log), (
                f"the {case} lookup allowed a non-search CRM operation"
            )
            assert_overlap(log, f"{case} lookup search stage")
            output = completed.stdout + completed.stderr
            assert expected_name in output and expected_region in output, (
                f"the {case} lookup failure did not identify its scope"
            )
            assert read_object(runtime / "records.json") == initial, (
                f"the {case} lookup changed account state"
            )
            assert read_object(runtime / "notices.json") == {
                "version": 1,
                "notices": [],
            }, f"the {case} lookup sent a notification"
            assert not (scenario / "reconciliation.md").exists(), (
                f"the {case} lookup produced a reconciliation report"
            )
            assert {path.name for path in scenario.iterdir()} == {
                ".harness",
                "crmctl",
                script.name,
            }, "a lookup failure left temporary working files behind"

    with tempfile.TemporaryDirectory(prefix="crm-verify-incomplete-") as temporary:
        scenario = Path(temporary)
        private = scenario / ".harness"
        runtime = private / "runtime"
        runtime.mkdir(parents=True)
        shutil.copy2(ROOT / "crmctl", scenario / "crmctl")
        shutil.copy2(script, scenario / script.name)
        initial = records_state("prospect")
        sunrise = next(
            row for row in initial["records"] if row.get("id") == "acct-central-sf-307"
        )
        sunrise.pop("status")
        write_object(runtime / "records.json", initial)
        write_object(runtime / "audit.json", {"version": 1, "operations": []})
        write_object(runtime / "notices.json", {"version": 1, "notices": []})
        (private / "lock").write_text("", encoding="utf-8")

        subprocess.run(
            [f"./{script.name}"],
            cwd=scenario,
            text=True,
            capture_output=True,
            timeout=12,
            check=False,
        )
        log = read_object(runtime / "audit.json").get("operations")
        assert isinstance(log, list) and len(log) == 4, (
            "a status-less complete record did not stop before CRM writes"
        )
        assert all(row.get("operation") == "search" for row in log[:2])
        assert all(row.get("operation") == "get" for row in log[2:])
        assert_overlap(log[:2], "status-less search stage")
        assert_overlap(log[2:], "status-less retrieval stage")
        assert max(row["finished_ns"] for row in log[:2]) <= min(
            row["started_ns"] for row in log[2:]
        ), "a status-less scenario violated the search/retrieval dependency"
        assert read_object(runtime / "records.json") == initial, (
            "a status-less complete record changed account state"
        )
        assert read_object(runtime / "notices.json") == {
            "version": 1,
            "notices": [],
        }, "a status-less complete record caused a notification"
        assert not (scenario / "reconciliation.md").exists(), (
            "a status-less complete record produced a reconciliation report"
        )
        assert {path.name for path in scenario.iterdir()} == {
            ".harness",
            "crmctl",
            script.name,
        }, "an incomplete-record failure left temporary working files behind"


def verify() -> None:
    assert_help_preceded_workflow(RUNTIME)
    assert_workflow(RUNTIME, sunrise_before="prospect", mode="changed")

    script = ROOT / "reconcile_accounts.sh"
    assert script.is_file() and not script.is_symlink(), "reconciliation program is missing"
    assert os.access(script, os.X_OK), "reconciliation program is not executable"
    script_text = script.read_text(encoding="utf-8")
    forbidden = (".harness", "records.json", "audit.json", "notices.json")
    assert not any(value in script_text for value in forbidden), (
        "the program bypasses the supported CRM executable"
    )
    assert_program_behavior(script)

    try:
        report = (ROOT / "reconciliation.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise AssertionError(f"reconciliation.md is missing: {exc}") from exc
    assert report == EXPECTED_REPORT, (
        "reconciliation.md has the wrong ordering, retrieved statuses, or action summary"
    )


def main() -> int:
    try:
        verify()
    except (AssertionError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: exact dependency-ordered CRM reconciliation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
