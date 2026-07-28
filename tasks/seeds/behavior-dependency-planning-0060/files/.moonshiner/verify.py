#!/usr/bin/env python3
"""Protected deterministic verifier for the fleet reconciliation task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = ROOT / "fleetctl"
DATABASE_PATH = ROOT / "fleet.db"
SEED_PATH = Path(__file__).resolve().with_name("fleet_seed.sql")
KEY_PATH = Path(__file__).resolve().with_name("audit.key")
SETUP_PATH = Path(__file__).resolve().with_name("setup.py")
JOURNAL_PATH = ROOT / ".fleet-audit" / "journal.jsonl"
DELIVERABLE_PATH = ROOT / "audit.md"

EXPECTED_CLIENT_SHA256 = "0531287d4682291f97e1e7ef81c509163ba25fa544f2f2bae70d4063ba8d5425"
EXPECTED_SEED_SHA256 = "aa1289df7fb7fb03d74684c9074505bc2cd8d6c2300364424f28818442a6b4c6"
EXPECTED_KEY_SHA256 = "f6278d9a7e7da66d0efd25e12caa24b11b2f383d47f7bee186bd3abedfd1ccdc"
EXPECTED_SETUP_SHA256 = "126dbbc54f15d6c1ac76b8cdf6d2e9015600fccdbaedfa76e222847c9ed59132"

TARGETS = [
    ("Box Truck 18", "Warehouse Fleet"),
    ("Passenger Van 23", "Programs Fleet"),
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
    require(DATABASE_PATH.is_file(), "fleet database is missing; run setup")
    actual = sqlite3.connect(f"{DATABASE_PATH.as_uri()}?mode=ro", uri=True)
    actual.row_factory = sqlite3.Row
    require(
        actual.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
        "fleet database is corrupt",
    )
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript(SEED_PATH.read_text(encoding="utf-8"))
        require(
            database_dump(actual) == database_dump(expected),
            "authoritative fleet state was modified",
        )
    finally:
        expected.close()
    return actual


def load_and_verify_journal() -> list[dict[str, Any]]:
    require(JOURNAL_PATH.is_file(), "no fleetctl execution journal found")
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
            require(
                entry.get("sequence") == line_number,
                "execution journal sequence is broken",
            )
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
    require(sha256(CLIENT_PATH) == EXPECTED_CLIENT_SHA256, "fleetctl was modified")
    require(sha256(SEED_PATH) == EXPECTED_SEED_SHA256, "protected fleet seed was modified")
    require(sha256(KEY_PATH) == EXPECTED_KEY_SHA256, "protected audit key was modified")
    require(sha256(SETUP_PATH) == EXPECTED_SETUP_SHA256, "protected setup was modified")

    connection = verify_database()
    try:
        authoritative_rows = connection.execute(
            """
            SELECT stable_id, name, location, status,
                   service_class, custodian, internal_note
              FROM vehicles
             ORDER BY stable_id
            """
        ).fetchall()
        authoritative = {row["stable_id"]: dict(row) for row in authoritative_rows}
    finally:
        connection.close()

    entries = load_and_verify_journal()
    require(
        len(entries) >= 5,
        "expected help discovery followed by two searches and two retrievals",
    )
    help_entries = entries[:-4]
    data_entries = entries[-4:]
    require(
        all(entry.get("operation") == "help" for entry in help_entries),
        "interface discovery was missing, late, or mixed with another operation",
    )
    require(
        [entry.get("operation") for entry in data_entries]
        == ["search", "search", "get", "get"],
        "fleet operations were extraneous, prohibited, or out of stage order",
    )
    require(all(entry.get("ok") is True for entry in entries), "a fleet operation failed")
    for entry in entries:
        require(
            isinstance(entry.get("started_ns"), int)
            and isinstance(entry.get("finished_ns"), int)
            and entry["started_ns"] < entry["finished_ns"],
            "execution journal timing is invalid",
        )
        require(
            isinstance(entry.get("pid"), int) and entry["pid"] > 0,
            "execution PID is invalid",
        )

    searches = data_entries[:2]
    gets = data_entries[2:]
    require(
        max(entry["finished_ns"] for entry in help_entries)
        <= min(entry["started_ns"] for entry in searches),
        "vehicle data was accessed before interface discovery finished",
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
    require(overlap(first_search, second_search), "the two searches were not concurrent")

    resolved_ids: list[str] = []
    for target in TARGETS:
        result_ids = searches_by_target[target].get("result_ids")
        require(
            isinstance(result_ids, list) and len(result_ids) == 1,
            f"search branch {target[0]} did not resolve uniquely",
        )
        stable_id = result_ids[0]
        require(
            isinstance(stable_id, str) and bool(stable_id),
            "a returned stable ID was empty",
        )
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
    require(overlap(gets[0], gets[1]), "the two retrievals were not concurrent")

    retrieved: list[dict[str, Any]] = []
    for target, stable_id in zip(TARGETS, resolved_ids):
        require(stable_id in authoritative, "a retrieved stable ID is not authoritative")
        journal_record = gets_by_id[stable_id].get("record")
        require(
            journal_record == authoritative[stable_id],
            "a retrieved record does not match authoritative fleet state",
        )
        require(
            (journal_record.get("name"), journal_record.get("location")) == target,
            "a retrieved record does not belong to its search branch",
        )
        retrieved.append(journal_record)

    first_status = retrieved[0]["status"]
    second_status = retrieved[1]["status"]
    comparison = (
        "same as Box Truck 18"
        if first_status == second_status
        else "different from Box Truck 18"
    )
    expected_output = (
        f"- Box Truck 18: {first_status}\n"
        f"- Passenger Van 23: {second_status}; comparison: {comparison}\n"
    )
    require(DELIVERABLE_PATH.is_file(), "audit.md is missing")
    try:
        actual_output = DELIVERABLE_PATH.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError("audit.md is not UTF-8") from exc
    require(
        actual_output == expected_output,
        "audit.md does not match the required exact format",
    )


def main() -> int:
    try:
        verify()
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError, VerificationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: fleet lookup behavior and exact-format audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
