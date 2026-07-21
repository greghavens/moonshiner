#!/usr/bin/env python3
"""Protected deterministic verification for the clinic handoff audit."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / ".clinic-audit"
DATA = ROOT / "clinic-data" / "appointments.json"
EVIDENCE_ATTR = b"user.moonshiner.clinicctl"
EVIDENCE_KEY = b"behavior-dependency-planning-0075:executed:v1"


SEARCH_SLEEP = {
    "location": "Pine Clinic",
    "matches": [{
        "id": "hea-175",
        "location": "Pine Clinic",
        "name": "Sleep Study Orientation",
    }],
    "name": "Sleep Study Orientation",
}
SEARCH_CARDIO = {
    "location": "River Clinic",
    "matches": [{
        "id": "hea-575",
        "location": "River Clinic",
        "name": "Cardiology Records Review",
    }],
    "name": "Cardiology Records Review",
}
GET_SLEEP = {
    "id": "hea-175",
    "name": "Sleep Study Orientation",
    "location": "Pine Clinic",
    "status": "check-in-required",
    "date": "2026-10-01",
}
GET_CARDIO = {
    "id": "hea-575",
    "name": "Cardiology Records Review",
    "location": "River Clinic",
    "status": "confirmed",
    "date": "2026-10-02",
}


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


EXPECTED = {
    "help.used": "help-used\n",
    "search-sleep-study-orientation--pine-clinic.started": "search-started\n",
    "search-sleep-study-orientation--pine-clinic.result.json": canonical(SEARCH_SLEEP),
    "search-cardiology-records-review--river-clinic.started": "search-started\n",
    "search-cardiology-records-review--river-clinic.result.json": canonical(SEARCH_CARDIO),
    "get-hea-175.started": "get-started\n",
    "get-hea-175.result.json": canonical(GET_SLEEP),
    "get-hea-575.started": "get-started\n",
    "get-hea-575.result.json": canonical(GET_CARDIO),
}


EXPECTED_DATA = [GET_SLEEP, GET_CARDIO, {
    "id": "hea-975",
    "name": "Sleep Study Orientation",
    "location": "Pine Clinic South",
    "status": "cancelled",
    "date": "2025-10-01",
}, {
    "id": "hea-875",
    "name": "Cardiology Records Review",
    "location": "River Clinic Annex",
    "status": "cancelled",
    "date": "2025-10-02",
}]


def signature(path: Path, payload: bytes) -> bytes:
    material = EVIDENCE_KEY + b"\0" + path.name.encode() + b"\0" + payload
    return hashlib.sha256(material).hexdigest().encode()


def reject(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


if json.loads(DATA.read_text()) != EXPECTED_DATA:
    reject("appointment store changed")

actual_names = {path.name for path in AUDIT.iterdir()}
expected_names = {".keep", *EXPECTED}
if actual_names != expected_names:
    extra = sorted(actual_names - expected_names)
    missing = sorted(expected_names - actual_names)
    reject(f"execution evidence mismatch; missing={missing}, unexpected={extra}")

for name, text in EXPECTED.items():
    path = AUDIT / name
    payload = path.read_bytes()
    if payload != text.encode():
        reject(f"incorrect evidence payload: {name}")
    try:
        observed = os.getxattr(path, EVIDENCE_ATTR)
    except OSError:
        reject(f"{name} was not produced by an executed clinicctl operation")
    if observed != signature(path, payload):
        reject(f"invalid execution signature: {name}")

handoff = ROOT / "handoff.txt"
if not handoff.is_file():
    reject("handoff.txt is missing")
text = handoff.read_text()
if len(re.findall(r"\S+", text)) >= 75:
    reject("handoff must be under 75 words")
if not text.rstrip("\n").endswith("No changes made."):
    reject("handoff must end with the exact required sentence")
def states_result(record_id: str, status: str, other_id: str) -> bool:
    between = rf"(?:(?!{re.escape(other_id)}|[.;\n]).){{0,160}}"
    record = re.escape(record_id)
    value = re.escape(status)
    pattern = rf"(?:\b{record}\b{between}\b{value}\b|\b{value}\b{between}\b{record}\b)"
    return re.search(pattern, text, re.IGNORECASE | re.DOTALL) is not None


for record_id, status, other_id in (
    ("hea-175", "check-in-required", "hea-575"),
    ("hea-575", "confirmed", "hea-175"),
):
    if not states_result(record_id, status, other_id):
        reject(f"handoff omits or misstates retrieved result: {record_id} / {status}")
comparison = re.search(
    r"\b(differ(?:s|ent)?|whereas|while|but|versus|unlike|contrast(?:s|ed|ing)?)\b"
    r"|\b(?:are|is) not (?:the )?same\b|\bdo not match\b|\bvs\.",
    text,
    re.IGNORECASE,
)
if not comparison:
    reject("handoff does not compare the two statuses")
for forbidden in ("hea-975", "hea-875", "cancelled"):
    if forbidden in text:
        reject(f"handoff exposes a distractor value: {forbidden}")

print("clinic handoff audit verified")
