#!/usr/bin/env python3
"""Protected deterministic verifier for the claims retrieval task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
TARGET_ID = "ins-136"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def expected_record() -> dict[str, str]:
    export = ROOT / "data" / "claims.xml"
    try:
        claims = ET.parse(export).getroot().findall("claim")
    except (OSError, ET.ParseError) as error:
        fail(f"protected claims export is unreadable: {error}")

    matches = [claim for claim in claims if claim.attrib.get("id") == TARGET_ID]
    if len(matches) != 1:
        fail("protected export must contain exactly one target id")
    target = matches[0]

    related = target.findtext("related")
    if not related or related == TARGET_ID:
        fail("protected export lacks a distinct related-claim distractor")
    if not any(claim.attrib.get("id") == related for claim in claims):
        fail("protected export's related claim is missing")

    record = {"id": target.attrib["id"]}
    for field in ("name", "location", "status", "related"):
        value = target.findtext(field)
        if value is None:
            fail(f"protected target is missing {field}")
        record[field] = value
    return record


def main() -> int:
    record = expected_record()
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    expected_file = (encoded + "\n").encode("utf-8")

    artifact = ROOT / "claim.json"
    if not artifact.is_file():
        fail("claim.json is missing")
    if artifact.read_bytes() != expected_file:
        fail("claim.json is not the exact minified target record")

    audit = ROOT / "audit.log"
    if not audit.is_file():
        fail("audit.log is missing; the genuine client was not used")
    try:
        lines = audit.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"audit.log is invalid: {error}")
    if len(events) != 1:
        fail("exactly one claims data operation is required")

    expected_event = {
        "found": True,
        "id": TARGET_ID,
        "operation": "get",
        "record_digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }
    if events[0] != expected_event:
        fail("the sole operation was not a full-record get for the XML id")

    runtime_export = ROOT / ".claim-runtime" / "claims.xml"
    if runtime_export.exists():
        fail("the claims store was modified")

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
