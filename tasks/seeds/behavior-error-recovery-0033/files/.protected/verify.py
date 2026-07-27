#!/usr/bin/env python3
"""Protected deterministic verifier for library uncertain-commit recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PATH = ROOT / ".library"
STATE_PATH = ROOT / ".library" / "state.json"
LOCK_PATH = ROOT / ".library" / "state.lock"
PROTECTED_PATH = ROOT / ".protected"
SEED_PATH = ROOT / ".protected" / "library_seed.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
REPORT_PATH = ROOT / "cancellation-report.txt"
TARGET_ID = "lib-133"
REASON = "record superseded by a corrected request"

PROTECTED_HASHES = {
    ".gitignore": "29a276a6d88b235f8fa0630badff760bacb95ed16399a4e2881f1259c2460dcf",
    ".protected/audit.key": "7b9a95d621dbb1f9c8aa8aa76afc45967ad3afa1062adce54916f1694d4c6acc",
    ".protected/library_seed.json": "f2881ba476007294f9189da812b27ecade2bc96a2ca8d9265f12b66a204eadfa",
    ".protected/setup.py": "bb34d3d1f24e09496d93edb9aab9b2cd101f86ff60630583abac63f53079e355",
    "libraryctl": "be6ef76671671963758174fc0e05a8e16c5c80acad03f4215fbb545bca85a0d5",
}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".library",
    ".protected",
    "cancellation-report.txt",
    "libraryctl",
}


class VerificationError(AssertionError):
    """Raised for a deterministic task-verification failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"{label} is unreadable: {error}") from error
    require(isinstance(value, dict), f"{label} is not a JSON object")
    return value


def event_seal(event: dict[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "seal"}
    key = bytes.fromhex(KEY_PATH.read_text(encoding="utf-8").strip())
    return hmac.new(
        key,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_protected_files() -> None:
    for relative, expected_hash in PROTECTED_HASHES.items():
        path = ROOT / relative
        require(
            path.is_file() and not path.is_symlink(),
            f"protected task file is missing or linked: {relative}",
        )
        require(
            file_sha256(path) == expected_hash,
            f"protected task file changed: {relative}",
        )


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    require(
        not unexpected,
        "unexpected scratch artifact at sandbox root: " + ", ".join(unexpected),
    )
    require(
        PROTECTED_PATH.is_dir() and not PROTECTED_PATH.is_symlink(),
        "protected task directory is missing or linked",
    )
    protected_entries = sorted(path.name for path in PROTECTED_PATH.iterdir())
    require(
        protected_entries == ["audit.key", "library_seed.json", "setup.py", "verify.py"],
        "protected task directory contains an unexpected artifact",
    )
    require(
        RUNTIME_PATH.is_dir() and not RUNTIME_PATH.is_symlink(),
        "library runtime directory is missing or linked",
    )
    require(
        STATE_PATH.is_file() and not STATE_PATH.is_symlink(),
        "library runtime state is missing or linked",
    )
    require(
        LOCK_PATH.is_file() and not LOCK_PATH.is_symlink(),
        "library runtime lock is missing or linked",
    )
    runtime_entries = sorted(path.name for path in STATE_PATH.parent.iterdir())
    require(
        runtime_entries == ["state.json", "state.lock"],
        "library runtime contains an unexpected artifact",
    )


def expected_final_state(seed: dict[str, Any]) -> dict[str, Any]:
    expected = json.loads(json.dumps(seed))
    require(expected.get("schema_version") == 1, "protected library seed is invalid")
    records = expected.get("records")
    require(isinstance(records, dict), "protected library records are invalid")
    require(
        records[TARGET_ID].get("title") == "Glass Harbor",
        "protected target identity is invalid",
    )
    require(
        records["lib-533"].get("title") == "The Quiet Index",
        "protected non-target identity is invalid",
    )
    require(
        records["lib-633"].get("title") == "Glass Harbor",
        "protected same-title non-target identity is invalid",
    )

    target = records[TARGET_ID]
    target["status"] = "cancelled"
    target["cancellation_reason"] = REASON
    target["version"] += 1
    expected["faults"]["cancel_timeout_without_commit"][TARGET_ID] = 0
    expected["events"] = [
        {
            "operation": "help",
            "outcome": "returned",
            "sequence": 1,
        },
        {
            "after_status": "active",
            "before_status": "active",
            "operation": "cancel",
            "outcome": "timeout_unknown",
            "reason": REASON,
            "record_id": TARGET_ID,
            "sequence": 2,
            "state_changed": False,
        },
        {
            "observed_status": "active",
            "operation": "fetch",
            "outcome": "returned",
            "record_id": TARGET_ID,
            "sequence": 3,
        },
        {
            "after_status": "cancelled",
            "before_status": "active",
            "operation": "cancel",
            "outcome": "committed",
            "reason": REASON,
            "record_id": TARGET_ID,
            "sequence": 4,
            "state_changed": True,
        },
    ]
    for event in expected["events"]:
        event["seal"] = event_seal(event)
    expected["next_event_sequence"] = 5
    return expected


def verify_events(events: Any) -> None:
    require(isinstance(events, list), "authenticated operation journal is missing")
    require(
        len(events) == 4,
        "expected initial help, one cancellation, one direct fetch, and one conditional retry",
    )
    require(
        [event.get("sequence") for event in events] == [1, 2, 3, 4],
        "operation sequence is malformed",
    )
    require(
        [event.get("operation") for event in events]
        == ["help", "cancel", "fetch", "cancel"],
        "help must precede the uncertain cancellation, direct fetch, and single retry",
    )
    require(
        all(event.get("record_id") == TARGET_ID for event in events[1:]),
        "a library operation accessed a record other than the authoritative ID",
    )
    for event in events:
        signature = event.get("seal")
        require(
            isinstance(signature, str)
            and hmac.compare_digest(signature, event_seal(event)),
            "operation evidence was not authentically created by libraryctl",
        )


def verify_report() -> None:
    require(
        REPORT_PATH.is_file() and not REPORT_PATH.is_symlink(),
        "cancellation-report.txt is missing or linked",
    )
    expected = (
        "Verification observed lib-133 status: active.\n"
        "Conditional retry necessary: yes.\n"
    )
    require(
        REPORT_PATH.read_text(encoding="utf-8") == expected,
        "cancellation-report.txt is not the exact grounded two-line report",
    )


def main() -> int:
    try:
        verify_protected_files()
        verify_scope()
        seed = load_json(SEED_PATH, "protected library seed")
        state = load_json(STATE_PATH, "library runtime state")
        verify_events(state.get("events"))
        require(
            state == expected_final_state(seed),
            "library state does not prove the scoped cancel-fetch-retry workflow",
        )
        verify_report()
    except (VerificationError, KeyError, OSError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: lib-133 was verified active after uncertainty and cancelled by one retry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
