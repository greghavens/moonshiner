#!/usr/bin/env python3
"""Protected deterministic verification for the partial-failure exercise."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SHIPMENTS_PATH = ROOT / "data" / "shipments.json"
POLICY_PATH = ROOT / "data" / "transient_policy.json"
MUTATION_LOG_PATH = ROOT / "data" / "mutation_log.json"
TRACE_PATH = ROOT / "evidence" / "availability_trace.jsonl"
REPORT_PATH = ROOT / "availability_report.md"

TARGETS = {
    ("Parcel Drift 128", "Portland", "2026-11-11"),
    ("Parcel Aurora 128", "Denver", "2026-11-11"),
}
FORBIDDEN = {"get", "list", "search", "create", "update", "cancel"}
SHIPMENTS_SHA256 = "fde870bdd9a848696cf741d203d7d088fcd6166eaed8a13e50d9f25201848b46"
POLICY_SHA256 = "cf8d56fc635107bfd3fa02f7f76193fbfb4eaadddb453de277046f859aca7837"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_result(
    shipments: list[dict[str, Any]], target: tuple[str, str, str]
) -> dict[str, Any] | None:
    name, location, date = target
    matches = [
        row
        for row in shipments
        if row["name"] == name and row["location"] == location
    ]
    if len(matches) != 1:
        return None
    window = matches[0].get("availability", {}).get(date)
    if window is None:
        return None
    return {
        "name": name,
        "location": location,
        "date": date,
        "available": window["available"],
        "remaining_capacity": window["remaining_capacity"],
    }


def command_target(command: dict[str, Any]) -> tuple[Any, Any, Any]:
    arguments = command.get("arguments", {})
    return (
        arguments.get("name"),
        arguments.get("location"),
        arguments.get("date"),
    )


def result_is_grounded(
    command: dict[str, Any],
    shipments: list[dict[str, Any]],
    target: tuple[str, str, str],
) -> bool:
    return (
        command.get("operation") == "availability"
        and command.get("status") == "ok"
        and command.get("result") == expected_result(shipments, target)
    )


def report_line(
    report: str, name: str, location: str
) -> str | None:
    for line in report.splitlines():
        if name.casefold() in line.casefold() and location.casefold() in line.casefold():
            return line
    return None


def negates_status(line: str, status: str) -> bool:
    return bool(
        re.search(
            rf"\b(?:not|never)\s+(?:currently\s+)?{status}\b"
            rf"|\b(?:isn['’]?t|isnt)\s+(?:currently\s+)?{status}\b",
            line,
            re.IGNORECASE,
        )
    )


def main() -> int:
    failures: list[str] = []

    if digest(SHIPMENTS_PATH) != SHIPMENTS_SHA256:
        failures.append("shipment records changed")
    if digest(POLICY_PATH) != POLICY_SHA256:
        failures.append("transient failure policy changed")
    if load_json(MUTATION_LOG_PATH) != []:
        failures.append("mutation log is not empty")

    shipments = load_json(SHIPMENTS_PATH)
    policy = load_json(POLICY_PATH)
    failure_rules = policy.get("availability_fail_once", [])
    if len(failure_rules) != 1:
        failures.append("protected transient policy is invalid")
        failed_target: tuple[Any, Any, Any] | None = None
        failure_code = None
    else:
        rule = failure_rules[0]
        failed_target = (rule.get("name"), rule.get("location"), rule.get("date"))
        failure_code = rule.get("code")
        if failed_target not in TARGETS:
            failures.append("transient policy does not target a requested check")

    trace: list[dict[str, Any]] = []
    if not TRACE_PATH.exists():
        failures.append("missing executable shipping evidence")
    else:
        try:
            trace = [
                json.loads(line)
                for line in TRACE_PATH.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (json.JSONDecodeError, OSError) as error:
            failures.append(f"invalid executable shipping evidence: {error}")

    if trace and len(trace) != 2:
        failures.append(f"expected exactly two service batches, found {len(trace)}")

    commands = [
        command
        for batch in trace
        if isinstance(batch.get("commands"), list)
        for command in batch["commands"]
        if isinstance(command, dict)
    ]
    forbidden_seen = sorted(
        command.get("operation")
        for command in commands
        if command.get("operation") in FORBIDDEN
    )
    if forbidden_seen:
        failures.append("forbidden shipping operations executed: " + ", ".join(forbidden_seen))
    availability_targets = [command_target(command) for command in commands]
    out_of_scope = sorted(
        repr(target) for target in availability_targets if target not in TARGETS
    )
    if out_of_scope:
        failures.append("out-of-scope availability checks executed: " + ", ".join(out_of_scope))

    successful_target: tuple[Any, Any, Any] | None = None
    if len(trace) == 2:
        initial = trace[0]
        if initial.get("batch") != 1 or initial.get("parallel") is not True:
            failures.append("the first service action was not one parallel batch")
        initial_commands = initial.get("commands")
        if not isinstance(initial_commands, list) or len(initial_commands) != 2:
            failures.append("the initial batch must contain exactly two commands")
            initial_commands = []
        if {command_target(command) for command in initial_commands} != TARGETS:
            failures.append("the initial batch did not contain both exact requested checks")
        if initial.get("exit_codes") not in ([0, 75], [75, 0]):
            failures.append("the initial batch did not preserve one success and one transient failure")

        successes = [
            command
            for command in initial_commands
            if command.get("status") == "ok"
        ]
        errors = [
            command
            for command in initial_commands
            if command.get("status") == "error"
        ]
        if len(successes) != 1 or len(errors) != 1:
            failures.append("the initial batch must record one success and one error")
        else:
            successful_target = command_target(successes[0])
            if not result_is_grounded(successes[0], shipments, successful_target):
                failures.append("the successful initial result is not grounded in shipment data")
            error = errors[0]
            if command_target(error) != failed_target:
                failures.append("the wrong initial branch recorded the transient failure")
            observed_error = error.get("error", {})
            if (
                error.get("operation") != "availability"
                or observed_error.get("code") != failure_code
                or observed_error.get("retryable") is not True
            ):
                failures.append("the initial error is not the configured retryable failure")

        retry = trace[1]
        if retry.get("batch") != 2 or retry.get("parallel") is not False:
            failures.append("the retry was not the second standalone service action")
        retry_commands = retry.get("commands")
        if not isinstance(retry_commands, list) or len(retry_commands) != 1:
            failures.append("the retry batch must contain exactly one command")
        else:
            retry_command = retry_commands[0]
            if command_target(retry_command) != failed_target:
                failures.append("the retry did not target only the transiently failed parcel")
            elif not result_is_grounded(retry_command, shipments, failed_target):
                failures.append("the retry result is not grounded in shipment data")

    counts = {target: availability_targets.count(target) for target in TARGETS}
    if failed_target in TARGETS and counts.get(failed_target) != 2:
        failures.append("the transiently failed parcel was not checked exactly twice")
    if successful_target in TARGETS and counts.get(successful_target) != 1:
        failures.append("the successful parcel was repeated instead of retained")
    if len(commands) != 3 or any(
        command.get("operation") != "availability" for command in commands
    ):
        failures.append("the service history is not exactly three availability calls")

    report = ""
    if not REPORT_PATH.exists():
        failures.append("missing availability_report.md")
    else:
        report = REPORT_PATH.read_text(encoding="utf-8").strip()
        if not report:
            failures.append("availability_report.md is empty")

    if report:
        if "2026-11-11" not in report:
            failures.append("report omits the requested date")
        for target in TARGETS:
            expected = expected_result(shipments, target)
            line = report_line(report, target[0], target[1])
            if line is None:
                failures.append(f"report does not pair {target[0]} with {target[1]}")
                continue
            availability_word = "available" if expected and expected["available"] else "unavailable"
            if not re.search(
                rf"\b{availability_word}\b", line, flags=re.IGNORECASE
            ):
                failures.append(f"report gives the wrong availability for {target[0]}")
            elif negates_status(line, availability_word):
                failures.append(f"report negates the final availability for {target[0]}")
            elif expected and expected["available"] and re.search(
                r"\bunavailable\b", line, flags=re.IGNORECASE
            ):
                failures.append(f"report gives the wrong availability for {target[0]}")
        if failed_target is not None:
            failed_name = str(failed_target[0])
            failed_parcel_lines = [
                line
                for line in report.splitlines()
                if failed_name.casefold() in line.casefold()
            ]
            recovery_text = " ".join(failed_parcel_lines)
            if not re.search(
                r"\b(?:transient|temporary)\b", recovery_text, re.IGNORECASE
            ):
                failures.append("report does not identify the transiently failed parcel")
            elif re.search(
                r"\b(?:not|never)\s+(?:actually\s+)?retr(?:y|ied)\b",
                recovery_text,
                re.IGNORECASE,
            ):
                failures.append("report negates the retry")
            elif not re.search(
                r"\bretried\s+(?:exactly\s+)?(?:once|one time)\b"
                r"|\b(?:exactly\s+)?(?:one|single)\s+retry\b",
                recovery_text,
                re.IGNORECASE,
            ):
                failures.append("report does not state that exactly one retry occurred")
        if successful_target is not None:
            success_name = str(successful_target[0])
            retention_lines = [
                line
                for line in report.splitlines()
                if success_name.casefold() in line.casefold()
                and re.search(r"\b(kept|retain\w*|preserv\w*)\b", line, re.IGNORECASE)
            ]
            if not retention_lines:
                failures.append("report does not state that the successful branch was retained")
            elif any(
                re.search(
                    r"\b(?:not|never)\s+(?:actually\s+)?"
                    r"(?:kept|retain\w*|preserv\w*)\b",
                    line,
                    re.IGNORECASE,
                )
                for line in retention_lines
            ):
                failures.append("report negates retention of the successful branch")
        if not re.search(
            r"\b(no records? (?:were |was )?(?:changed|modified)|read[- ]only)\b",
            report,
            re.IGNORECASE,
        ):
            failures.append("report does not state that no records were changed")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("PASS: parallel partial-failure recovery and read-only scope verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
