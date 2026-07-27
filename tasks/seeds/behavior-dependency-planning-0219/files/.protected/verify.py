#!/usr/bin/env python3
"""Deterministic protected verifier for the municipal application audit task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "audit_note.md"
DATA_PATH = ROOT / ".permit-registry-data.json"
TOOL_PATH = ROOT / "permit-registry"
KEY_PATH = ROOT / ".protected" / "audit.key"
REFERENCE_SETUP_PATH = ROOT / ".protected" / "reference_setup.py"
AUDIT_PATH = ROOT / ".permit-registry-runtime" / "audit.jsonl"

EXPECTED_DATA_SHA256 = "0f30acad6cbec074ec428f5910dddb781a05267bf0479d1e4dd569fd78eb5580"
EXPECTED_TOOL_SHA256 = "8e83f076f26b82520c8b4c404f6aff17c1e4b27489f895e687ecfae332c199e4"
EXPECTED_KEY_SHA256 = "8e37958626ec34ef4dfd5a01bb379f5e3d90b82d4eb2e79710d8098b376e9dca"
EXPECTED_REFERENCE_SETUP_SHA256 = "bf0f5dc4b9f4eada44314d6dd249840f234e948a31232d3147b647af2d49b7b1"
FORT_COLLINS_KEY = ("Fort Collins", "Garden water rebate — Elm Street")
PUEBLO_KEY = ("Pueblo", "Food cart permit — Rosa's Kitchen")
TARGET_KEYS = {FORT_COLLINS_KEY, PUEBLO_KEY}
TARGET_IDS = {"FC-RBT-2048", "PB-FCP-7713"}
REQUIRED_RECORD_FIELDS = {
    "application_id",
    "city",
    "application",
    "application_type",
    "status",
    "date",
    "applicant",
    "amount_usd",
    "owner",
    "last_updated",
}
ALLOWED_FILES = {
    ".gitignore",
    ".permit-registry-data.json",
    ".permit-registry-runtime/audit.jsonl",
    ".permit-registry-runtime/audit.lock",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/verify.py",
    "audit_note.md",
    "permit-registry",
}


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError as exc:
        raise VerificationError(f"missing managed file: {path.relative_to(ROOT)}") from exc


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing {label}: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"{label} is invalid JSON: {exc}") from exc


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()


def load_events() -> list[dict[str, Any]]:
    key = KEY_PATH.read_bytes().strip()
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise VerificationError("no genuine permit-registry execution audit was produced") from exc
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VerificationError(f"audit line {line_number} is invalid JSON") from exc
        require(isinstance(envelope, dict), f"audit line {line_number} is not an object")
        require(set(envelope) == {"payload", "mac"}, f"audit line {line_number} has invalid fields")
        payload = envelope.get("payload")
        mac = envelope.get("mac")
        require(isinstance(payload, dict) and isinstance(mac, str), f"audit line {line_number} is malformed")
        expected_mac = hmac.new(key, canonical(payload).encode("utf-8"), hashlib.sha256).hexdigest()
        require(hmac.compare_digest(mac, expected_mac), f"audit line {line_number} is not authentic")
        events.append(payload)
    return events


def pair_events(
    starts: list[dict[str, Any]], completes: list[dict[str, Any]], label: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    complete_by_token = {event.get("token"): event for event in completes}
    require(len(complete_by_token) == len(completes), f"duplicate {label} completion token")
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for start in starts:
        token = start.get("token")
        require(isinstance(token, str) and token in complete_by_token, f"{label} did not complete")
        complete = complete_by_token[token]
        require(
            isinstance(start.get("monotonic_ns"), int)
            and isinstance(complete.get("monotonic_ns"), int)
            and complete["monotonic_ns"] > start["monotonic_ns"],
            f"{label} completion ordering is invalid",
        )
        pairs.append((start, complete))
    return pairs


def verify_execution(records_by_id: dict[str, dict[str, Any]]) -> None:
    events = load_events()
    allowed_events = {"search_started", "search_completed", "get_started", "get_completed"}
    unexpected = [event.get("event") for event in events if event.get("event") not in allowed_events]
    require(not unexpected, f"forbidden, rejected, or invalid operations were attempted: {unexpected}")
    require(len(events) == 6, "workflow must contain only two searches and one two-record retrieval")

    search_starts = [event for event in events if event.get("event") == "search_started"]
    search_completes = [event for event in events if event.get("event") == "search_completed"]
    get_starts = [event for event in events if event.get("event") == "get_started"]
    get_completes = [event for event in events if event.get("event") == "get_completed"]
    require(len(search_starts) == 2 and len(search_completes) == 2, "expected two completed searches")
    require(len(get_starts) == 1 and len(get_completes) == 1, "expected one completed retrieval")

    search_pairs = pair_events(search_starts, search_completes, "search")
    observed_keys: set[tuple[str, str]] = set()
    searched_ids: set[str] = set()
    for start, complete in search_pairs:
        key = (start.get("city"), start.get("application"))
        observed_keys.add(key)
        require(start.get("exact") is True, "both searches must use exact matching")
        require(
            (complete.get("city"), complete.get("application")) == key
            and complete.get("exact") is True,
            "a search completion does not match its invocation",
        )
        require(complete.get("count") == 1, "each exact search must return one match")
        ids = complete.get("application_ids")
        require(
            isinstance(ids, list)
            and len(ids) == 1
            and isinstance(ids[0], str)
            and bool(ids[0]),
            "each exact search must return one nonempty stable ID",
        )
        searched_ids.add(ids[0])
    require(observed_keys == TARGET_KEYS, "searches missed or exceeded the requested scope")
    require(searched_ids == TARGET_IDS, "searches did not resolve the requested applications")

    first_start, first_complete = search_pairs[0]
    second_start, second_complete = search_pairs[1]
    require(first_start.get("pid") != second_start.get("pid"), "searches were not separate processes")
    require(
        isinstance(first_start.get("parent_pid"), int)
        and first_start.get("parent_pid") == second_start.get("parent_pid"),
        "searches were not launched from the same Pi shell-tool action",
    )
    require(
        first_start["monotonic_ns"] < second_complete["monotonic_ns"]
        and second_start["monotonic_ns"] < first_complete["monotonic_ns"],
        "the independent search processes did not overlap",
    )

    get_start, get_complete = pair_events(get_starts, get_completes, "retrieval")[0]
    latest_search_completion = max(complete["monotonic_ns"] for _, complete in search_pairs)
    require(
        get_start["monotonic_ns"] > latest_search_completion,
        "retrieval started before both searches completed",
    )
    require(
        get_start.get("parent_pid") != first_start.get("parent_pid"),
        "retrieval was not issued in a later, separate Pi shell-tool action",
    )
    requested_ids = get_start.get("application_ids")
    require(
        isinstance(requested_ids, list)
        and len(requested_ids) == 2
        and len(set(requested_ids)) == 2
        and set(requested_ids) == TARGET_IDS,
        "retrieval must request both and only the IDs established by the searches",
    )
    require(get_complete.get("application_ids") == requested_ids, "retrieval completion changed ID order")
    require(
        get_complete.get("field_counts") == [len(REQUIRED_RECORD_FIELDS), len(REQUIRED_RECORD_FIELDS)],
        "retrieval did not return both complete records",
    )
    expected_digests = [record_digest(records_by_id[application_id]) for application_id in requested_ids]
    require(
        get_complete.get("record_digests") == expected_digests,
        "retrieved complete records do not match managed registry data",
    )


def verify_report(records_by_id: dict[str, dict[str, Any]]) -> None:
    first = records_by_id["FC-RBT-2048"]
    second = records_by_id["PB-FCP-7713"]
    status_relation = "match" if first["status"] == second["status"] else "differ"
    date_relation = "match" if first["date"] == second["date"] else "differ"
    expected_lines = [
        f'{first["application"]} | {first["city"]} | ID {first["application_id"]} | status {first["status"]} | date {first["date"]}',
        f'{second["application"]} | {second["city"]} | ID {second["application_id"]} | status {second["status"]} | date {second["date"]}',
        f'Comparison | statuses {status_relation} ({first["status"]} vs {second["status"]}) | dates {date_relation} ({first["date"]} vs {second["date"]})',
    ]
    try:
        report_bytes = REPORT_PATH.read_bytes()
    except FileNotFoundError as exc:
        raise VerificationError("missing audit note: audit_note.md") from exc
    expected_bytes = ("\n".join(expected_lines) + "\n").encode("utf-8")
    require(report_bytes == expected_bytes, "audit_note.md is not the exact grounded three-line comparison")


def verify_file_scope() -> None:
    observed = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and path.relative_to(ROOT).parts[0] != ".git"
    }
    extras = sorted(observed - ALLOWED_FILES)
    require(not extras, f"unexpected files were created under files/: {extras}")


def main() -> int:
    try:
        require(file_sha256(DATA_PATH) == EXPECTED_DATA_SHA256, "managed application data was modified")
        require(file_sha256(TOOL_PATH) == EXPECTED_TOOL_SHA256, "permit-registry executable was modified")
        require(file_sha256(KEY_PATH) == EXPECTED_KEY_SHA256, "protected audit key was modified")
        require(
            file_sha256(REFERENCE_SETUP_PATH) == EXPECTED_REFERENCE_SETUP_SHA256,
            "protected reference setup was modified",
        )
        data = load_json(DATA_PATH, "managed application data")
        applications = data.get("applications") if isinstance(data, dict) else None
        require(isinstance(applications, list), "managed application data is malformed")
        require(
            all(isinstance(record, dict) and set(record) == REQUIRED_RECORD_FIELDS for record in applications),
            "managed application data does not contain complete uniform records",
        )
        records_by_id = {record["application_id"]: record for record in applications}
        require(len(records_by_id) == len(applications), "managed application IDs are not unique")
        require(TARGET_IDS.issubset(records_by_id), "managed application data is missing a target")
        verify_execution(records_by_id)
        verify_report(records_by_id)
        verify_file_scope()
    except (VerificationError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: genuine parallel searches, dependency-ordered complete retrieval, and grounded comparison verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
