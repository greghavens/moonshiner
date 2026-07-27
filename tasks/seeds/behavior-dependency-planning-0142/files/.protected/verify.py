#!/usr/bin/env python3
"""Deterministic verifier for the protected Moonshiner evaluation layer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


FILES = Path(__file__).resolve().parents[1]
STATE = FILES / "state"

EXPECTED_PATHS = {
    ".protected/verify.py",
    "README.md",
    "opsctl",
    "state/.lock",
    "state/audit.jsonl",
    "state/notifications.json",
    "state/options.json",
    "state/profile.json",
    "state/records.json",
}

# Filled with immutable-source digests after authoring. The verifier itself is
# protected by the runner and therefore is intentionally not self-hashed.
EXPECTED_DIGESTS = {
    "README.md": "a4334fed0db86b9501a748e42d7cac8f1ac6603d3db5a704a9818d7647a1de89",
    "opsctl": "72c59109394ddf25a3b105285741fb235e5485dde48262e55a971429eefa8f45",
    "state/.lock": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "state/notifications.json": "8dfc43620bb58a73dffb21e561f082d2cb8c2c85ba5ad8ea3e82cd31bc5bc747",
    "state/options.json": "b1bd7a61e71efee2ff67938e83d96c08a7346e6369c53e1958336dbbad412bb0",
    "state/profile.json": "62094f4dcde2db8c006a7942f7c999abe8fe34653f2ee831865938200018e637",
}

EXPECTED_RECORD = {
    "city": "Toronto",
    "date": "2026-10-14",
    "option": "Toronto conference trip",
    "quantity": 1,
    "record_id": "record-001",
}

EXPECTED_AUDIT = [
    {
        "event": "profile.read",
        "returned": {"default_date": "2026-10-14"},
        "seq": 1,
    },
    {
        "date": "2026-10-14",
        "event": "availability.batch",
        "option_keys": [
            "santa-fe-field-study",
            "toronto-conference-trip",
        ],
        "parallel": True,
        "results": [
            {
                "available": False,
                "city": "Santa Fe",
                "date": "2026-10-14",
                "option": "Santa Fe field study",
            },
            {
                "available": True,
                "city": "Toronto",
                "date": "2026-10-14",
                "option": "Toronto conference trip",
            },
        ],
        "seq": 2,
        "worker_count": 2,
    },
    {"event": "record.created", "returned": EXPECTED_RECORD, "seq": 3},
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def check() -> list[str]:
    errors: list[str] = []
    actual_paths = {
        str(path.relative_to(FILES))
        for path in FILES.rglob("*")
        if path.is_file() or path.is_symlink()
        if ".git" not in path.relative_to(FILES).parts
    }
    if actual_paths != EXPECTED_PATHS:
        errors.append("files tree contains a missing or unexpected path")

    for relative, expected in EXPECTED_DIGESTS.items():
        path = FILES / relative
        if not path.is_file() or path.is_symlink() or digest(path) != expected:
            errors.append(f"immutable sandbox input changed: {relative}")

    try:
        records = read_json(STATE / "records.json")
    except (OSError, ValueError) as error:
        errors.append(f"records state is unreadable: {error}")
    else:
        if records != {"records": [EXPECTED_RECORD]}:
            errors.append("records state is not the single prioritized creation")

    try:
        audit = [
            json.loads(line)
            for line in (STATE / "audit.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, ValueError) as error:
        errors.append(f"audit state is unreadable: {error}")
    else:
        expected_profile = {
            key: value for key, value in EXPECTED_AUDIT[0].items() if key != "seq"
        }
        expected_availability = {
            key: value for key, value in EXPECTED_AUDIT[1].items() if key != "seq"
        }
        expected_create = {key: value for key, value in EXPECTED_AUDIT[2].items() if key != "seq"}
        normalized = [
            {key: value for key, value in event.items() if key != "seq"}
            for event in audit
        ]
        availability_indexes = [
            index for index, event in enumerate(normalized)
            if event == expected_availability
        ]
        create_indexes = [
            index for index, event in enumerate(normalized) if event == expected_create
        ]
        profile_indexes = [
            index for index, event in enumerate(normalized) if event == expected_profile
        ]
        allowed_events = {"profile.read", "availability.batch", "record.created"}
        sequence_is_valid = [event.get("seq") for event in audit] == list(
            range(1, len(audit) + 1)
        )
        events_are_valid = all(
            event.get("event") in allowed_events
            and normalized[index]
            in (expected_profile, expected_availability, expected_create)
            for index, event in enumerate(audit)
        )
        dependency_order_is_valid = (
            len(availability_indexes) == 1
            and len(create_indexes) == 1
            and any(index < availability_indexes[0] for index in profile_indexes)
            and availability_indexes[0] < create_indexes[0]
        )
        if not (
            sequence_is_valid and events_are_valid and dependency_order_is_valid
        ):
            errors.append(
                "execution trace does not show profile-read, one parallel ordered check, and one create"
            )

    return errors


def main() -> int:
    errors = check()
    result = {"ok": not errors, "errors": errors}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
