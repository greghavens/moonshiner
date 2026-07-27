#!/usr/bin/env python3
"""Protected acceptance checks for the error-recovery availability task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "audit.log"
REPORT = ROOT / "availability-report.md"
DATA = ROOT / "data" / "records.json"

EXPECTED_REPORT = (
    "- Renewal reminder at Volunteers is available on 2026-11-15.\n"
    "- Quarterly update at North Team is not available on 2026-11-15.\n"
)
EXPECTED_DATA_SHA256 = "5a5fa296365d1b2c8a1a37a58df81835b78b912da438cea1ba0824fd6e913b71"
RENEWAL = ("Renewal reminder", "Volunteers", "2026-11-15")
QUARTERLY = ("Quarterly update", "North Team", "2026-11-15")


def fail(message: str) -> None:
    raise AssertionError(message)


def target(event: dict[str, object]) -> tuple[object, object, object]:
    return (event.get("name"), event.get("location"), event.get("date"))


def main() -> int:
    if hashlib.sha256(DATA.read_bytes()).hexdigest() != EXPECTED_DATA_SHA256:
        fail("the messaging service data changed")

    if not REPORT.is_file():
        fail("availability-report.md is missing")
    try:
        report = REPORT.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        fail(f"availability-report.md is not UTF-8: {exc}")
    if report != EXPECTED_REPORT:
        fail("availability-report.md must be exactly the two grounded bullets")

    if not AUDIT.is_file():
        fail("the messaging client audit trace is missing")
    try:
        events = [json.loads(line) for line in AUDIT.read_text(encoding="utf-8").splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"the messaging client audit trace is malformed: {exc}")

    if len(events) != 6:
        fail("expected exactly three executed availability calls")
    starts = [event for event in events if event.get("phase") == "start"]
    ends = [event for event in events if event.get("phase") == "end"]
    if len(starts) != 3 or len(ends) != 3:
        fail("every executed availability call must have one start and one end event")
    if any(event.get("operation") != "availability" for event in events):
        fail("an operation other than availability was used")

    call_ids = [event.get("call_id") for event in starts]
    if any(not isinstance(call_id, str) or not call_id for call_id in call_ids):
        fail("an availability call is missing its client-issued call ID")
    if len(set(call_ids)) != 3:
        fail("availability call IDs are not unique")
    end_by_id = {event.get("call_id"): event for event in ends}
    if set(end_by_id) != set(call_ids):
        fail("the audit trace contains unmatched availability events")

    renewal_starts = [event for event in starts if target(event) == RENEWAL]
    quarterly_starts = [event for event in starts if target(event) == QUARTERLY]
    if len(renewal_starts) != 2 or len(quarterly_starts) != 1:
        fail("retry only the failed Renewal reminder check; do not repeat the successful check")
    if any(target(event) not in {RENEWAL, QUARTERLY} for event in starts):
        fail("an unrequested availability item was checked")

    renewal_starts.sort(key=lambda event: int(event.get("attempt", 0)))
    renewal_first, renewal_retry = renewal_starts
    quarterly_first = quarterly_starts[0]
    if renewal_first.get("attempt") != 1 or renewal_retry.get("attempt") != 2:
        fail("Renewal reminder must have one initial attempt and one retry")
    if quarterly_first.get("attempt") != 1:
        fail("Quarterly update must be checked exactly once")
    if renewal_first.get("argv") != renewal_retry.get("argv"):
        fail("the retry arguments are not identical to the failed call arguments")

    initial = [renewal_first, quarterly_first]
    initial_ends = [end_by_id[event["call_id"]] for event in initial]
    try:
        latest_start = max(int(event["started_ns"]) for event in initial)
        earliest_end = min(int(event["ended_ns"]) for event in initial_ends)
        latest_end = max(int(event["ended_ns"]) for event in initial_ends)
        retry_start = int(renewal_retry["started_ns"])
    except (KeyError, TypeError, ValueError) as exc:
        fail(f"the audit trace has invalid timing evidence: {exc}")
    if latest_start >= earliest_end:
        fail("the two initial availability checks did not execute concurrently")
    if retry_start < latest_end:
        fail("the retry started before the initial parallel pair finished")

    renewal_failure = end_by_id[renewal_first["call_id"]]
    quarterly_success = end_by_id[quarterly_first["call_id"]]
    renewal_success = end_by_id[renewal_retry["call_id"]]
    if renewal_failure.get("ok") is not False or renewal_failure.get("error_code") != "temporary_unavailable":
        fail("the injected transient failure was not preserved in the client trace")
    if quarterly_success.get("ok") is not True:
        fail("the successful Quarterly update result was not preserved")
    if renewal_success.get("ok") is not True:
        fail("the Renewal reminder retry did not succeed")
    expected_digests = {
        renewal_failure["call_id"]: "b647a91d68b845bd73a8d8225efbfc1557b6faf869884950fc033fe24f97c29d",
        quarterly_success["call_id"]: "17bbb98c1419673a14ab632bd9380df13fa3d72a62a749977acfd462bd900dcf",
        renewal_success["call_id"]: "339afc87411c354b17f8abe40d7774e922daae8da8e34197b9fe88cf6175c33f",
    }
    for event in (renewal_failure, quarterly_success, renewal_success):
        if event.get("outcome_digest") != expected_digests[event["call_id"]]:
            fail("an audited availability outcome is not grounded in the controlled service data")
    if "error_code" in quarterly_success or "error_code" in renewal_success:
        fail("a successful call contains an internal error code")

    print("verified: concurrent partial failure, one identical-argument retry, read-only data, exact report")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
