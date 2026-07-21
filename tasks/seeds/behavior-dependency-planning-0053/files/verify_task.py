#!/usr/bin/env python3
"""Protected verification for the circulation audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "catalog.json"
EVENTS = ROOT / "__pycache__" / "library_audit" / "events.jsonl"
REPORT = ROOT / "audit.md"
TARGETS = [
    ("Harbor Signals Atlas", "Coastal Branch"),
    ("The Small Museum Handbook", "University Branch"),
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path.name}: {error}")


if not REPORT.is_file():
    fail("audit.md is missing")
if not EVENTS.is_file():
    fail("no executable catalog audit was recorded")

catalog = load_json(CATALOG)
try:
    rows = [json.loads(line) for line in EVENTS.read_text(encoding="utf-8").splitlines() if line]
except (OSError, json.JSONDecodeError) as error:
    fail(f"catalog audit log is invalid: {error}")

if len(rows) != 8:
    fail(f"expected exactly eight catalog audit events, found {len(rows)}")

previous = "0" * 64
for index, row in enumerate(rows, start=1):
    digest = row.get("digest")
    unsigned = dict(row)
    unsigned.pop("digest", None)
    if row.get("seq") != index or row.get("previous") != previous:
        fail("catalog audit event chain is out of order")
    if digest != hashlib.sha256(canonical(unsigned)).hexdigest():
        fail("catalog audit event digest is invalid")
    previous = digest

kinds = [row.get("kind") for row in rows]
if kinds[:2] != ["search_start", "search_start"]:
    fail("the first stage was not two concurrent searches")
if sorted(kinds[2:4]) != ["search_complete", "search_complete"]:
    fail("both searches must complete before the get stage")
if kinds[4:6] != ["get_start", "get_start"]:
    fail("the second stage was not two concurrent gets")
if sorted(kinds[6:8]) != ["get_complete", "get_complete"]:
    fail("both full-record gets must complete")
if max(row["time_ns"] for row in rows[:2]) >= min(row["time_ns"] for row in rows[2:4]):
    fail("search executions did not overlap")
if max(row["time_ns"] for row in rows[2:4]) >= min(row["time_ns"] for row in rows[4:6]):
    fail("a get began before both searches returned")
if max(row["time_ns"] for row in rows[4:6]) >= min(row["time_ns"] for row in rows[6:8]):
    fail("get executions did not overlap")
if len({row.get("pid") for row in rows[:2]}) != 2 or len({row.get("pid") for row in rows[4:6]}) != 2:
    fail("each parallel stage must use two genuine command processes")
search_parents = {row.get("ppid") for row in rows[:2]}
get_parents = {row.get("ppid") for row in rows[4:6]}
if len(search_parents) != 1 or len(get_parents) != 1:
    fail("each parallel pair must be launched by one Bash process")
search_parent = next(iter(search_parents))
get_parent = next(iter(get_parents))
if not isinstance(search_parent, int) or search_parent <= 1:
    fail("the search pair was not launched by a live Bash process")
if not isinstance(get_parent, int) or get_parent <= 1:
    fail("the get pair was not launched by a live Bash process")
if search_parent == get_parent:
    fail("the get pair was not launched in the next Bash action")

searches = {(row.get("name"), row.get("location")): row for row in rows[2:4]}
if set(searches) != set(TARGETS):
    fail("searches did not use exactly the two requested name-and-location pairs")

unique_ids = {}
for target in TARGETS:
    matches = searches[target].get("result", {}).get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        fail(f"requested search did not resolve uniquely: {target[0]}")
    match = matches[0]
    if (match.get("name"), match.get("location")) != target or not match.get("id"):
        fail(f"search result does not match its requested branch: {target[0]}")
    if set(match) != {"id", "name", "location"}:
        fail("a search result was treated as a full record")
    unique_ids[target] = match["id"]

get_starts = {row.get("id") for row in rows[4:6]}
if get_starts != set(unique_ids.values()):
    fail("a get did not use the sole ID from its own search")
get_results = {row.get("id"): row.get("result") for row in rows[6:8]}
if set(get_results) != set(unique_ids.values()):
    fail("full retrieved records do not correspond to both unique searches")

records = []
for target in TARGETS:
    stable_id = unique_ids[target]
    result = get_results[stable_id]
    stored = next((record for record in catalog if record.get("id") == stable_id), None)
    if result != stored or (result.get("name"), result.get("location")) != target:
        fail(f"retrieved record does not match protected catalog data: {target[0]}")
    if not isinstance(result.get("status"), str) or not result["status"]:
        fail(f"retrieved record has no status: {target[0]}")
    records.append(result)

relation = "the same" if records[0]["status"] == records[1]["status"] else "different"
expected = (
    "## First record\n"
    f"{records[0]['name']} at {records[0]['location']} has stable ID `{records[0]['id']}` "
    f"and retrieved status `{records[0]['status']}`.\n\n"
    "## Second record\n"
    f"{records[1]['name']} at {records[1]['location']} has stable ID `{records[1]['id']}` "
    f"and retrieved status `{records[1]['status']}`.\n\n"
    f"The first status is `{records[0]['status']}` and the second is `{records[1]['status']}`; "
    f"the statuses are {relation}.\n"
)
actual = REPORT.read_text(encoding="utf-8")
if actual != expected:
    fail("audit.md does not match the required headings, order, facts, and comparison")

print("PASS: executable catalog workflow and exact audit report verified")
