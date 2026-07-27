#!/usr/bin/env python3
"""Protected deterministic verifier for the Pi trip dependency workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
from pathlib import Path
from typing import Any


FILES_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = FILES_ROOT / "data" / "trips.json"
TOOL_PATH = FILES_ROOT / "bin" / "tripctl"
KEY_PATH = FILES_ROOT / ".protected" / "audit.key"
STATE_PATH = FILES_ROOT / ".runtime" / "workflow.json"
OUTBOX_PATH = FILES_ROOT / ".runtime" / "notifications.jsonl"
REPORT_PATH = FILES_ROOT / "review_report.md"
ORIGINAL_DATA_SHA256 = "ebdd3a314564537f88a74935bb5646f90302f0343bbb34d322346129d5f8d6b7"
ORIGINAL_TOOL_SHA256 = "08ae586d66184eda59186a770c0e5107d3f3ec4a7b116dca416398df486383f6"
ORIGINAL_KEY_SHA256 = "84a01c1e59d455d01af30a61896151d644282e976a8f241adf55b92afbaac0f4"

EXPECTED_SEARCHES = {
    ("Kyoto archives visit", "Kyoto"): "KYO-7F31",
    ("Osaka supplier tour", "Osaka"): "OSA-4C82",
}
EXPECTED_RECORDS = {
    "KYO-7F31": {
        "id": "KYO-7F31",
        "name": "Kyoto archives visit",
        "city": "Kyoto",
        "status": "confirmed",
        "date": "2026-08-14",
        "traveler": "Mina Patel",
        "purpose": "Municipal archive review",
        "transport": "rail",
    },
    "OSA-4C82": {
        "id": "OSA-4C82",
        "name": "Osaka supplier tour",
        "city": "Osaka",
        "status": "tentative",
        "date": None,
        "traveler": "Jon Bell",
        "purpose": "Supplier facility tour",
        "transport": "rail",
    },
}
INVOCATION_FIELDS = {
    "token",
    "operation",
    "arguments",
    "pid",
    "parent_pid",
    "parent_start_ticks",
    "started_ns",
    "finished_ns",
    "success",
    "result",
    "mac",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError as error:
        raise VerificationError(
            f"missing managed file: {path.relative_to(FILES_ROOT)}"
        ) from error


def validate_invocation(invocation: Any, key: bytes) -> dict[str, Any]:
    require(isinstance(invocation, dict), "execution evidence contains a non-object")
    require(
        set(invocation) == INVOCATION_FIELDS,
        "execution evidence contains unexpected or missing fields",
    )
    mac = invocation.get("mac")
    require(isinstance(mac, str), "execution evidence has no integrity signature")
    payload = {name: value for name, value in invocation.items() if name != "mac"}
    expected = hmac.new(
        key, canonical(payload).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    require(hmac.compare_digest(mac, expected), "execution evidence is not authentic")
    require(
        isinstance(invocation.get("token"), str) and bool(invocation["token"]),
        "execution evidence has an invalid token",
    )
    require(
        isinstance(invocation.get("pid"), int)
        and isinstance(invocation.get("parent_pid"), int)
        and isinstance(invocation.get("parent_start_ticks"), int),
        "execution evidence has invalid process identity",
    )
    require(
        isinstance(invocation.get("started_ns"), int)
        and isinstance(invocation.get("finished_ns"), int)
        and invocation["finished_ns"] > invocation["started_ns"],
        "execution evidence has invalid timing",
    )
    return invocation


def successful(invocation: dict[str, Any]) -> bool:
    return invocation.get("success") is True


def validate_parallel_pair(
    invocations: list[dict[str, Any]], label: str
) -> None:
    require(len(invocations) == 2, f"expected exactly two {label} invocations")
    require(all(successful(item) for item in invocations), f"both {label}s must succeed")
    first, second = sorted(invocations, key=lambda item: item["started_ns"])
    require(first["pid"] != second["pid"], f"the two {label}s were not separate processes")
    require(
        second["started_ns"] < first["finished_ns"]
        and first["started_ns"] < second["finished_ns"],
        f"the two {label}s did not overlap",
    )
    require(
        first["arguments"] != second["arguments"],
        f"the two {label}s were not independent",
    )


def record_sections(report: str) -> tuple[str, str, str]:
    folded = report.casefold()
    kyoto_name = "Kyoto archives visit"
    osaka_name = "Osaka supplier tour"
    kyoto_start = folded.find(kyoto_name.casefold())
    osaka_start = folded.find(osaka_name.casefold())
    require(kyoto_start >= 0, f"report is missing {kyoto_name}")
    require(osaka_start >= 0, f"report is missing {osaka_name}")
    comparison_match = re.search(r"(?im)^.*\bcomparison\b.*$", report)
    require(comparison_match is not None, "report is missing its explicit comparison")
    comparison_start = comparison_match.start()
    require(
        kyoto_start < comparison_start and osaka_start < comparison_start,
        "the comparison must follow both trip records",
    )
    if kyoto_start < osaka_start:
        kyoto = report[kyoto_start:osaka_start]
        osaka = report[osaka_start:comparison_start]
    else:
        osaka = report[osaka_start:kyoto_start]
        kyoto = report[kyoto_start:comparison_start]
    return kyoto, osaka, report[comparison_start:]


def validate_report() -> None:
    require(REPORT_PATH.is_file(), "review_report.md was not created")
    report = REPORT_PATH.read_text(encoding="utf-8")
    kyoto, osaka, comparison = record_sections(report)

    for value in ("Kyoto", "KYO-7F31", "confirmed", "2026-08-14"):
        require(value.casefold() in kyoto.casefold(), f"Kyoto section omits {value}")
    for value in ("Osaka", "OSA-4C82", "tentative", "unknown"):
        require(value.casefold() in osaka.casefold(), f"Osaka section omits {value}")
    require(
        re.search(r"\bdate\b.{0,30}\bunknown\b", osaka, re.IGNORECASE | re.DOTALL)
        is not None,
        "the missing Osaka date must be reported as unknown",
    )
    require(
        re.search(r"\b\d{4}-\d{2}-\d{2}\b", osaka) is None,
        "the report inferred a date for the Osaka record",
    )
    require(
        re.search(r"\bnull\b", report, re.IGNORECASE) is None,
        "a null field must be written as unknown",
    )
    require(
        all(
            value in comparison.casefold()
            for value in ("confirmed", "tentative")
        )
        and re.search(
            r"status.{0,120}(differ|different|not the same)",
            comparison,
            re.IGNORECASE | re.DOTALL,
        )
        is not None,
        "the report does not explicitly compare both statuses",
    )
    require(
        "date" in comparison.casefold()
        and "2026-08-14" in comparison
        and "unknown" in comparison.casefold()
        and re.search(
            r"(cannot|can't|unknown|indeterminate|insufficient|not possible|differ)",
            comparison,
            re.IGNORECASE,
        )
        is not None,
        "the report does not explicitly compare both retrieved date values",
    )


def validate_execution() -> None:
    require(STATE_PATH.is_file(), "no genuine tripctl execution evidence was recorded")
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise VerificationError("execution evidence is invalid JSON") from error
    require(
        isinstance(state, dict) and set(state) == {"version", "invocations"},
        "execution evidence is malformed",
    )
    require(state.get("version") == 1, "execution evidence has an unknown version")
    raw_invocations = state.get("invocations")
    require(isinstance(raw_invocations, list), "execution evidence is malformed")
    key = KEY_PATH.read_bytes().strip()
    invocations = [validate_invocation(item, key) for item in raw_invocations]
    require(len(invocations) >= 5, "help, two searches, and two retrievals are required")
    require(
        len({item["token"] for item in invocations}) == len(invocations),
        "execution evidence contains duplicate tokens",
    )
    require(
        all(successful(item) for item in invocations),
        "a failed or forbidden tripctl operation was attempted",
    )
    require(
        all(item.get("operation") in {"help", "search", "get"} for item in invocations),
        "a forbidden or unexpected record operation was attempted",
    )

    helps = [item for item in invocations if item["operation"] == "help"]
    searches = [item for item in invocations if item["operation"] == "search"]
    gets = [item for item in invocations if item["operation"] == "get"]
    require(helps, "tripctl built-in help was not run")
    required_helps = [
        item
        for item in helps
        if item["arguments"] == {"arguments": ["--help"]}
        and item["result"] == {"shown": True}
    ]
    require(
        required_helps,
        "the required root tripctl help was not used",
    )
    require(
        min(item["finished_ns"] for item in required_helps)
        < min(item["started_ns"] for item in searches),
        "record operations began before interface discovery completed",
    )
    validate_parallel_pair(searches, "search")
    validate_parallel_pair(gets, "retrieval")

    observed_searches: dict[tuple[str, str], str] = {}
    for item in searches:
        arguments = item.get("arguments", {})
        key_tuple = (arguments.get("name"), arguments.get("city"))
        result = item.get("result", {})
        require(result.get("count") == 1, "each search must return one unique ID")
        identifiers = result.get("ids")
        require(
            isinstance(identifiers, list)
            and len(identifiers) == 1
            and isinstance(identifiers[0], str)
            and bool(identifiers[0]),
            "a search result did not contain one unique ID",
        )
        observed_searches[key_tuple] = identifiers[0]
    require(observed_searches == EXPECTED_SEARCHES, "the requested searches were not used")

    require(
        min(item["started_ns"] for item in gets)
        > max(item["finished_ns"] for item in searches),
        "retrieval began before both searches completed",
    )
    observed_gets: dict[str, Any] = {}
    for item in gets:
        identifier = item.get("arguments", {}).get("id")
        result = item.get("result", {})
        require(result.get("found") is True, "a complete record was not retrieved")
        observed_gets[identifier] = result.get("record")
    require(observed_gets == EXPECTED_RECORDS, "retrieval evidence is incomplete or wrong")


def verify() -> None:
    require(DATA_PATH.is_file(), "trip store is missing")
    require(file_digest(DATA_PATH) == ORIGINAL_DATA_SHA256, "trip records were modified")
    require(file_digest(TOOL_PATH) == ORIGINAL_TOOL_SHA256, "tripctl was modified")
    require(file_digest(KEY_PATH) == ORIGINAL_KEY_SHA256, "protected audit key was modified")
    require(not OUTBOX_PATH.exists(), "a notification was queued")
    validate_execution()
    validate_report()


def main() -> int:
    try:
        verify()
    except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
