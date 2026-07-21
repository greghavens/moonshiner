#!/usr/bin/env python3
"""Protected acceptance checks for the stockroom dependency-planning audit."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".stockroom"

EXPECTED_RECORDS = [
    {"id": "inv-127", "name": "Cold-Chain Gel Packs", "location": "Medical Stores",
     "status": "low-stock", "date": "2026-08-07"},
    {"id": "inv-527", "name": "Tamper-Evident Labels", "location": "Returns Center",
     "status": "in-stock", "date": "2026-08-08"},
    {"id": "inv-927", "name": "Cold-Chain Gel Packs",
     "location": "Medical Stores Overflow", "status": "discontinued",
     "date": "2025-08-07"},
    {"id": "inv-827", "name": "Tamper-Evident Labels",
     "location": "Returns Center Overflow", "status": "discontinued",
     "date": "2025-08-08"},
]
EXPECTED_PREFERENCES = {
    "default_location": "Central Receiving",
    "replenishment_view": "weekly",
}


def fail() -> None:
    print("FAIL: the protected stockroom audit contract is not satisfied", file=sys.stderr)
    raise SystemExit(1)


def load(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def one_sentence(line: str) -> bool:
    return bool(line) and line[-1] in ".!?" and not re.search(r"[.!?]", line[:-1])


def main() -> None:
    try:
        if load(".stockroom/records.json") != EXPECTED_RECORDS:
            fail()
        if load(".stockroom/preferences.json") != EXPECTED_PREFERENCES:
            fail()
        if load(".stockroom/notifications.json") != []:
            fail()
        if load(".stockroom/mutation_log.json") != []:
            fail()

        actions = load(".stockroom/session.json").get("actions")
        if not isinstance(actions, list) or len(actions) != 2:
            fail()
        search, get = actions
        if search.get("operation") != "search" or search.get("parallel") is not True:
            fail()
        if get.get("operation") != "get" or get.get("parallel") is not True:
            fail()

        expected_pairs = [
            ("Cold-Chain Gel Packs", "Medical Stores"),
            ("Tamper-Evident Labels", "Returns Center"),
        ]
        search_requests = search.get("requests")
        search_results = search.get("results")
        if (not isinstance(search_requests, list) or len(search_requests) != 2
                or not isinstance(search_results, list) or len(search_results) != 2):
            fail()

        stable_ids_by_pair = {}
        for request, result in zip(search_requests, search_results):
            if not isinstance(request, dict):
                fail()
            pair = (request.get("name"), request.get("location"))
            if (request != {"name": pair[0], "location": pair[1]}
                    or pair not in expected_pairs or pair in stable_ids_by_pair):
                fail()
            matches = result.get("matches") if isinstance(result, dict) else None
            if not isinstance(matches, list) or len(matches) != 1:
                fail()
            match = matches[0]
            if match.get("name") != pair[0] or match.get("location") != pair[1]:
                fail()
            stable_id = match.get("id")
            if not isinstance(stable_id, str) or not stable_id:
                fail()
            stable_ids_by_pair[pair] = stable_id

        if len(stable_ids_by_pair) != 2 or len(set(stable_ids_by_pair.values())) != 2:
            fail()
        get_requests = get.get("requests")
        get_results = get.get("results")
        if (not isinstance(get_requests, list) or len(get_requests) != 2
                or not isinstance(get_results, list) or len(get_results) != 2):
            fail()
        pairs_by_stable_id = {
            stable_id: pair for pair, stable_id in stable_ids_by_pair.items()
        }
        retrieved_by_pair = {}
        for request, result in zip(get_requests, get_results):
            if not isinstance(request, dict):
                fail()
            stable_id = request.get("id")
            if (request != {"id": stable_id} or stable_id not in pairs_by_stable_id
                    or pairs_by_stable_id[stable_id] in retrieved_by_pair):
                fail()
            record = result.get("record") if isinstance(result, dict) else None
            if not isinstance(record, dict) or record.get("id") != stable_id:
                fail()
            retrieved_by_pair[pairs_by_stable_id[stable_id]] = record

        if len(retrieved_by_pair) != 2:
            fail()

        report = (ROOT / "audit.txt").read_text(encoding="utf-8")
        lines = report.splitlines()
        if len(lines) != 3 or not all(one_sentence(line) for line in lines):
            fail()
        first, second = (retrieved_by_pair[pair] for pair in expected_pairs)
        if not all(value in lines[0] for value in
                   (first["name"], first["location"], first["status"])):
            fail()
        if not all(value in lines[1] for value in
                   (second["name"], second["location"], second["status"])):
            fail()
        if not all(value in lines[2] for value in (first["status"], second["status"])):
            fail()
        if re.search(r"\b(recommend|should|consider|suggest|replenish|order)\b", report,
                     re.IGNORECASE):
            fail()
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        fail()
    print("PASS: stockroom audit workflow and report verified")


if __name__ == "__main__":
    main()
