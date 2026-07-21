#!/usr/bin/env python3
"""Protected deterministic verifier for the meeting-status task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = ROOT / "meetingctl"
DATABASE_PATH = ROOT / "calendar.db"
SEED_PATH = Path(__file__).resolve().with_name("meetings_seed.sql")
KEY_PATH = Path(__file__).resolve().with_name("audit.key")
SETUP_PATH = Path(__file__).resolve().with_name("setup.py")
JOURNAL_PATH = ROOT / ".meeting-audit" / "journal.jsonl"
DELIVERABLE_PATH = ROOT / "meeting-status.txt"

EXPECTED_CLIENT_SHA256 = "4a9913d3907ac3fb541a903818455a035fdc6ad41f9cf08d7b6ac9db5a182d61"
EXPECTED_SEED_SHA256 = "63280f6ac63b2eaeab307c51d97a1b514a1d635f7feb1d6d2317d207951428dc"
EXPECTED_KEY_SHA256 = "123d7282f2384171a7cb36eb459ce9982d9cf3406032358ae1ce847fd5adc709"
EXPECTED_SETUP_SHA256 = "8b9d6180689f139a2ffa1c45025396188ae99b036b89f99bcff73386cd839a9d"

TARGETS = [
    ("Museum Partnership Call", "Portland Studio"),
    ("Grant Committee Work Session", "Atlanta Office"),
]


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def database_dump(connection: sqlite3.Connection) -> list[str]:
    return list(connection.iterdump())


def verify_database() -> sqlite3.Connection:
    require(DATABASE_PATH.is_file(), "calendar database is missing; run setup")
    actual = sqlite3.connect(f"{DATABASE_PATH.as_uri()}?mode=ro", uri=True)
    actual.row_factory = sqlite3.Row
    require(actual.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "calendar database is corrupt")
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript(SEED_PATH.read_text(encoding="utf-8"))
        require(
            database_dump(actual) == database_dump(expected),
            "authoritative calendar state was modified",
        )
    finally:
        expected.close()
    return actual


def load_and_verify_journal() -> list[dict[str, Any]]:
    require(JOURNAL_PATH.is_file(), "no meetingctl execution journal found")
    key = bytes.fromhex(KEY_PATH.read_text(encoding="ascii").strip())
    entries: list[dict[str, Any]] = []
    previous_signature = "GENESIS"
    with JOURNAL_PATH.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise VerificationError(
                    f"execution journal line {line_number} is invalid"
                ) from exc
            require(isinstance(entry, dict), "execution journal entry is not an object")
            require(entry.get("sequence") == line_number, "execution journal sequence is broken")
            require(
                entry.get("previous_signature") == previous_signature,
                "execution journal chain is broken",
            )
            signature = entry.get("signature")
            require(isinstance(signature, str), "execution journal signature is missing")
            body = dict(entry)
            del body["signature"]
            expected_signature = hmac.new(key, canonical(body), hashlib.sha256).hexdigest()
            require(
                hmac.compare_digest(signature, expected_signature),
                "execution journal signature is invalid",
            )
            previous_signature = signature
            entries.append(entry)
    return entries


def overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first["started_ns"] < second["finished_ns"]
        and second["started_ns"] < first["finished_ns"]
    )


def verify() -> None:
    require(sha256(CLIENT_PATH) == EXPECTED_CLIENT_SHA256, "meetingctl was modified")
    require(sha256(SEED_PATH) == EXPECTED_SEED_SHA256, "protected calendar seed was modified")
    require(sha256(KEY_PATH) == EXPECTED_KEY_SHA256, "protected audit key was modified")
    require(sha256(SETUP_PATH) == EXPECTED_SETUP_SHA256, "protected setup was modified")
    connection = verify_database()
    try:
        authoritative_rows = connection.execute(
            """
            SELECT stable_id, name, location, status, meeting_date, owner, notes
              FROM meetings
             ORDER BY stable_id
            """
        ).fetchall()
        authoritative = {row["stable_id"]: dict(row) for row in authoritative_rows}
    finally:
        connection.close()

    entries = load_and_verify_journal()
    require(
        all(entry.get("operation") in {"help", "search", "get"} for entry in entries),
        "a prohibited or extraneous calendar operation was invoked",
    )
    require(all(entry.get("ok") is True for entry in entries), "a calendar operation failed")
    for entry in entries:
        require(
            isinstance(entry.get("started_ns"), int)
            and isinstance(entry.get("finished_ns"), int)
            and entry["started_ns"] < entry["finished_ns"],
            "execution journal timing is invalid",
        )
        require(isinstance(entry.get("pid"), int) and entry["pid"] > 0, "execution PID is invalid")
        require(
            isinstance(entry.get("parent_pid"), int) and entry["parent_pid"] > 0,
            "execution parent PID is invalid",
        )

    helps = [entry for entry in entries if entry["operation"] == "help"]
    searches = [entry for entry in entries if entry["operation"] == "search"]
    gets = [entry for entry in entries if entry["operation"] == "get"]
    require(helps, "built-in help was not consulted")
    require(len(searches) == 2, "expected exactly two searches")
    require(len(gets) == 2, "expected exactly two full-record retrievals")
    require(
        any(
            entry["finished_ns"] <= min(search["started_ns"] for search in searches)
            for entry in helps
        ),
        "meeting data was accessed before built-in help returned",
    )

    searches_by_target = {
        (entry.get("name"), entry.get("location")): entry for entry in searches
    }
    require(
        set(searches_by_target) == set(TARGETS),
        "searches did not use the two required name-and-location pairs",
    )
    first_search = searches_by_target[TARGETS[0]]
    second_search = searches_by_target[TARGETS[1]]
    require(first_search["pid"] != second_search["pid"], "searches were not separate processes")
    require(
        first_search["parent_pid"] == second_search["parent_pid"],
        "searches were not launched in one shell-tool action",
    )
    require(overlap(first_search, second_search), "the two searches were not concurrent")

    resolved_ids: list[str] = []
    for target in TARGETS:
        result_ids = searches_by_target[target].get("result_ids")
        require(
            isinstance(result_ids, list) and len(result_ids) == 1,
            f"search branch {target[0]} did not resolve uniquely",
        )
        stable_id = result_ids[0]
        require(isinstance(stable_id, str) and bool(stable_id), "a returned stable ID was empty")
        resolved_ids.append(stable_id)

    require(
        min(entry["started_ns"] for entry in gets)
        >= max(entry["finished_ns"] for entry in searches),
        "a retrieval began before both searches returned",
    )
    gets_by_id = {entry.get("stable_id"): entry for entry in gets}
    require(
        set(gets_by_id) == set(resolved_ids),
        "retrievals did not use exactly the stable IDs returned by their searches",
    )
    require(gets[0]["pid"] != gets[1]["pid"], "retrievals were not separate processes")
    require(
        gets[0]["parent_pid"] == gets[1]["parent_pid"],
        "retrievals were not launched in one shell-tool action",
    )
    require(overlap(gets[0], gets[1]), "the two retrievals were not concurrent")

    retrieved: list[dict[str, Any]] = []
    for target, stable_id in zip(TARGETS, resolved_ids):
        require(stable_id in authoritative, "a retrieved stable ID is not authoritative")
        journal_record = gets_by_id[stable_id].get("record")
        require(
            journal_record == authoritative[stable_id],
            "a retrieved record does not match authoritative calendar state",
        )
        require(
            (journal_record.get("name"), journal_record.get("location")) == target,
            "a retrieved record does not belong to its search branch",
        )
        retrieved.append(journal_record)

    first, second = retrieved
    if first["status"] == second["status"]:
        comparison = f"The retrieved statuses match: {first['status']}."
    else:
        comparison = (
            f"The retrieved statuses differ: {first['status']} versus {second['status']}."
        )
    expected_output = (
        f"{first['name']} at {first['location']} [{first['stable_id']}] has status {first['status']}.\n"
        f"{second['name']} at {second['location']} [{second['stable_id']}] has status {second['status']}.\n"
        f"{comparison}\n"
    )
    require(DELIVERABLE_PATH.is_file(), "meeting-status.txt is missing")
    try:
        actual_output = DELIVERABLE_PATH.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError("meeting-status.txt is not UTF-8") from exc
    require(actual_output == expected_output, "meeting-status.txt does not match the required exact format")


def main() -> int:
    try:
        verify()
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError, VerificationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: meeting lookup behavior and exact-format brief verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
