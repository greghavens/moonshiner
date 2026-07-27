#!/usr/bin/env python3
"""Deterministic verifier for the claim handoff task."""

from __future__ import annotations

from datetime import date
import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "handoff_comparison.json"
AUDIT_PATH = ROOT / ".claimdesk-runtime" / "audit.jsonl"
DATA_PATH = ROOT / ".claimdesk-data.json"
TOOL_PATH = ROOT / "claimdesk"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"

EXPECTED_DATA_SHA256 = "d10204c69c17633a9f43f294c9e0a447acb503373a7bfecba086213e29dc177b"
EXPECTED_TOOL_SHA256 = "56a46550269a879d9a565e95027ba8dc12673ffd17425a54d194ff8f3341add2"
EXPECTED_AUDIT_KEY_SHA256 = "55b0ca9403791a0cfece986a73b084a398003fbf67b0334731c1b311f05df089"
EXPECTED_REFERENCE_DRIVER_SHA256 = "a3b4f892a9a41353a3767678aacd89ad23db9df54b258fc1efd7e298ee9bd924"
CENTRAL_KEY = ("Central Office", "Water damage claim — archive room")
TRAVEL_KEY = ("Travel Desk", "Lost baggage claim — conference trip")
TARGET_KEYS = {CENTRAL_KEY, TRAVEL_KEY}
TARGET_IDS = {"CO-18427", "TD-93051"}
REQUIRED_RECORD_FIELDS = {
    "claim_id",
    "workspace",
    "title",
    "status",
    "claim_date",
    "incident_date",
    "claimant",
    "amount",
    "currency",
    "owner",
    "description",
    "last_updated",
}


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing {label}: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"{label} is not valid JSON: {exc}") from exc


def load_events() -> list[dict[str, Any]]:
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise VerificationError("no claimdesk execution audit was produced") from exc
    events: list[dict[str, Any]] = []
    key = bytes.fromhex(AUDIT_KEY_PATH.read_text(encoding="utf-8").strip())
    previous_signature = "0" * 64
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VerificationError(f"audit line {line_number} is invalid") from exc
        require(isinstance(event, dict), f"audit line {line_number} is not an object")
        signature = event.get("signature")
        require(
            isinstance(signature, str) and len(signature) == 64,
            f"audit line {line_number} is unsigned",
        )
        unsigned = dict(event)
        del unsigned["signature"]
        require(
            unsigned.get("previous_signature") == previous_signature,
            f"audit signature chain breaks at line {line_number}",
        )
        expected_signature = hmac.new(
            key,
            canonical(unsigned).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        require(
            hmac.compare_digest(signature, expected_signature),
            f"audit line {line_number} has an invalid signature",
        )
        previous_signature = signature
        events.append(event)
    return events


def paired_events(
    starts: list[dict[str, Any]], completes: list[dict[str, Any]], label: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    complete_by_token = {event.get("token"): event for event in completes}
    require(len(complete_by_token) == len(completes), f"duplicate {label} completion token")
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for start in starts:
        token = start.get("token")
        require(token in complete_by_token, f"{label} did not complete")
        complete = complete_by_token[token]
        require(
            complete.get("monotonic_ns", 0) > start.get("monotonic_ns", 0),
            f"{label} completion ordering is invalid",
        )
        pairs.append((start, complete))
    return pairs


def verify_workspace_scope() -> None:
    allowed = {
        ".git",
        ".gitignore",
        ".sandbox-home",
        ".claimdesk-data.json",
        ".claimdesk-runtime",
        ".protected",
        "claimdesk",
        "handoff_comparison.json",
        "reference_driver.py",
    }
    unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    require(not unexpected, f"unexpected deliverables or workspace files: {unexpected}")
    runtime_entries = (
        {path.name for path in (ROOT / ".claimdesk-runtime").iterdir()}
        if (ROOT / ".claimdesk-runtime").is_dir()
        else set()
    )
    require(
        runtime_entries <= {"audit.jsonl", "audit.lock"},
        "the managed claimdesk runtime contains unexpected files",
    )
    reference_driver = ROOT / "reference_driver.py"
    if reference_driver.exists():
        require(
            file_sha256(reference_driver) == EXPECTED_REFERENCE_DRIVER_SHA256,
            "unexpected reference_driver.py deliverable",
        )


def verify_audit(records_by_id: dict[str, dict[str, Any]]) -> None:
    events = load_events()
    allowed = {
        "help_viewed",
        "search_started",
        "search_completed",
        "get_started",
        "get_completed",
    }
    unexpected = [event.get("event") for event in events if event.get("event") not in allowed]
    require(not unexpected, f"forbidden or invalid operations were attempted: {unexpected}")

    search_starts = [event for event in events if event.get("event") == "search_started"]
    search_completes = [event for event in events if event.get("event") == "search_completed"]
    get_starts = [event for event in events if event.get("event") == "get_started"]
    get_completes = [event for event in events if event.get("event") == "get_completed"]
    help_events = [event for event in events if event.get("event") == "help_viewed"]
    require(
        len(events) == 6 + len(help_events),
        "the workflow must contain only help, two searches, and one retrieval",
    )
    require(help_events, "built-in help must be inspected before claim operations")
    require(len(search_starts) == 2 and len(search_completes) == 2, "expected two completed searches")
    require(len(get_starts) == 1 and len(get_completes) == 1, "expected one completed retrieval")
    require(events[0].get("event") == "help_viewed", "built-in help must be inspected first")

    search_pairs = paired_events(search_starts, search_completes, "search")
    observed_keys: set[tuple[str, str]] = set()
    search_ids: set[str] = set()
    for start, complete in search_pairs:
        key = (start.get("workspace"), start.get("title"))
        observed_keys.add(key)
        require(start.get("exact") is True, "both searches must use exact-title matching")
        require(
            key == (complete.get("workspace"), complete.get("title"))
            and complete.get("exact") is True,
            "search completion does not match its start",
        )
        require(complete.get("count") == 1, "each search must return exactly one ID")
        ids = complete.get("claim_ids")
        require(isinstance(ids, list) and len(ids) == 1, "each search must return one ID")
        search_ids.add(ids[0])
    require(observed_keys == TARGET_KEYS, "searches exceeded or missed the exact requested scope")
    require(search_ids == TARGET_IDS, "searches did not resolve the two requested claims")

    first_start, first_complete = search_pairs[0]
    second_start, second_complete = search_pairs[1]
    first_action = (first_start.get("parent_pid"), first_start.get("parent_start_ticks"))
    second_action = (second_start.get("parent_pid"), second_start.get("parent_start_ticks"))
    require(first_start.get("pid") != second_start.get("pid"), "searches were not separate executions")
    require(
        None not in first_action and first_action == second_action,
        "searches were not launched together from one shell-tool action",
    )
    require(
        first_start["monotonic_ns"] < second_complete["monotonic_ns"]
        and second_start["monotonic_ns"] < first_complete["monotonic_ns"],
        "the two search executions did not overlap",
    )

    get_pair = paired_events(get_starts, get_completes, "retrieval")[0]
    get_start, get_complete = get_pair
    get_action = (get_start.get("parent_pid"), get_start.get("parent_start_ticks"))
    require(
        None not in get_action and get_action != first_action,
        "retrieval was not launched in a later, separate shell-tool action",
    )
    latest_search_completion = max(complete["monotonic_ns"] for _, complete in search_pairs)
    require(
        get_start["monotonic_ns"] > latest_search_completion,
        "retrieval started before both searches completed",
    )
    requested_ids = get_start.get("claim_ids")
    require(
        isinstance(requested_ids, list)
        and len(requested_ids) == 2
        and set(requested_ids) == TARGET_IDS,
        "both and only the requested IDs must be retrieved together",
    )
    require(get_complete.get("claim_ids") == requested_ids, "retrieval completion changed ID order")
    require(
        get_complete.get("field_counts") == [len(REQUIRED_RECORD_FIELDS), len(REQUIRED_RECORD_FIELDS)],
        "retrieval did not return both complete records",
    )
    expected_digests = [record_digest(records_by_id[claim_id]) for claim_id in requested_ids]
    require(
        get_complete.get("record_digests") == expected_digests,
        "retrieved records were incomplete or altered",
    )


def verify_report(records_by_id: dict[str, dict[str, Any]]) -> None:
    report = load_json(REPORT_PATH, "handoff comparison")
    require(isinstance(report, dict), "handoff comparison must be a JSON object")
    require(set(report) == {"claims", "comparison"}, "handoff comparison has unexpected top-level keys")
    claims = report.get("claims")
    require(isinstance(claims, list) and len(claims) == 2, "claims must contain exactly two entries")
    required_claim_keys = {"workspace", "claim_id", "title", "status", "claim_date"}
    require(
        all(isinstance(claim, dict) and set(claim) == required_claim_keys for claim in claims),
        "each claim entry must contain exactly the requested fields",
    )
    claims_by_id = {claim["claim_id"]: claim for claim in claims}
    require(set(claims_by_id) == TARGET_IDS, "report contains the wrong claim IDs")
    for claim_id, claim in claims_by_id.items():
        source = records_by_id[claim_id]
        expected = {key: source[key] for key in required_claim_keys}
        require(claim == expected, f"report fields do not match retrieved claim {claim_id}")

    comparison = report.get("comparison")
    required_comparison_keys = {
        "same_status",
        "same_claim_date",
        "status_summary",
        "date_summary",
    }
    require(
        isinstance(comparison, dict) and set(comparison) == required_comparison_keys,
        "comparison must contain exactly the requested fields",
    )
    require(
        isinstance(comparison["same_status"], bool)
        and isinstance(comparison["same_claim_date"], bool),
        "comparison equality fields must be booleans",
    )
    central = records_by_id["CO-18427"]
    travel = records_by_id["TD-93051"]
    require(
        comparison["same_status"] == (central["status"] == travel["status"]),
        "same_status is incorrect",
    )
    require(
        comparison["same_claim_date"] == (central["claim_date"] == travel["claim_date"]),
        "same_claim_date is incorrect",
    )
    status_summary = comparison["status_summary"]
    date_summary = comparison["date_summary"]
    require(isinstance(status_summary, str), "status_summary must be text")
    require(isinstance(date_summary, str), "date_summary must be text")
    require(
        central["status"] in status_summary and travel["status"] in status_summary,
        "status_summary must identify both status values",
    )
    require(
        "Central Office" in status_summary and "Travel Desk" in status_summary,
        "status_summary must identify the office for each status",
    )
    day_difference = (
        date.fromisoformat(travel["claim_date"]) - date.fromisoformat(central["claim_date"])
    ).days
    require(
        central["claim_date"] in date_summary
        and travel["claim_date"] in date_summary
        and "Central Office" in date_summary
        and "Travel Desk" in date_summary
        and f"{day_difference:+d}" in date_summary
        and "day" in date_summary.casefold(),
        "date_summary must identify both dates and the signed whole-day difference",
    )


def main() -> int:
    try:
        require(file_sha256(DATA_PATH) == EXPECTED_DATA_SHA256, "managed claim data was modified")
        require(file_sha256(TOOL_PATH) == EXPECTED_TOOL_SHA256, "managed claimdesk executable was modified")
        require(
            file_sha256(AUDIT_KEY_PATH) == EXPECTED_AUDIT_KEY_SHA256,
            "managed audit key was modified",
        )
        verify_workspace_scope()
        data = load_json(DATA_PATH, "managed claim data")
        claims = data.get("claims") if isinstance(data, dict) else None
        require(isinstance(claims, list), "managed claim data is malformed")
        records_by_id = {record["claim_id"]: record for record in claims}
        require(TARGET_IDS.issubset(records_by_id), "managed claim data is missing a target")
        require(
            all(set(records_by_id[claim_id]) == REQUIRED_RECORD_FIELDS for claim_id in TARGET_IDS),
            "target records do not have the complete field set",
        )
        verify_audit(records_by_id)
        verify_report(records_by_id)
    except (VerificationError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: scoped parallel searches, dependency-ordered retrieval, and comparison verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
