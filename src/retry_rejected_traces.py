#!/usr/bin/env python3
"""Retrace and rescreen current quality rejections before publication.

A trace that failed the deterministic screen or the independent judge writes a
rejection review. This repair lane re-runs each such seed with the exact prior
findings fed back to the teacher (``trace_task(..., feedback=...)``), then
rescreens the replacement. A rejection clears only when a fresh trace both passes
verification and is accepted by the judge; otherwise it rotates behind the other
rejections (oldest-first) so one hard task never monopolizes the lane.

Metered: it drives the configured teacher and judge runtimes. It defers cleanly
on a usage-limit backoff and never overwrites a newer, not-yet-screened passing
trace (that belongs to the first-pass screen, not the repair lane).
  python3 src/retry_rejected_traces.py                  # all current rejections
  python3 src/retry_rejected_traces.py --limit 5        # oldest five
  python3 src/retry_rejected_traces.py --max-attempts 2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import TRACES, load_seeds
from generate_traces import trace_task
from runtimes import get_judge, get_teacher
from runtimes.availability import ModelUnavailable, require_available
from screen_traces import feedback_from_review, screen
from review_contract import is_accepted

REJECTED = {"deterministic_reject", "review_reject"}
REVIEWS = TRACES / "reviews"
META = TRACES / "meta"


def current_rejection(task_id: str) -> dict | None:
    """The seed's review record iff it is a standing rejection."""
    path = REVIEWS / f"{task_id}.json"
    if not path.exists():
        return None
    try:
        review = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return review if review.get("status") in REJECTED else None


def has_unscreened_passing_trace(task_id: str, review: dict) -> bool:
    """True if a newer passing trace exists that this rejection never screened.

    A replacement trace overwrites meta in place; if the latest meta passes and
    its hashes differ from the ones the rejection pinned, it is a first-pass
    candidate for the screen, not repair work — leave it alone.
    """
    try:
        meta = json.loads((META / f"{task_id}.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    if meta.get("passed") is not True or meta.get("protected_intact") is not True:
        return False
    return (meta.get("raw_sha256") != review.get("raw_sha256")
            or meta.get("diff_sha256") != review.get("diff_sha256"))


def rejected_seeds(seeds: list[dict], limit: int = 0) -> list[dict]:
    """Standing rejections, oldest review first so failures rotate fairly."""
    candidates = []
    for seed in seeds:
        review = current_rejection(seed["id"])
        if review is None or has_unscreened_passing_trace(seed["id"], review):
            continue
        mtime = (REVIEWS / f"{seed['id']}.json").stat().st_mtime_ns
        candidates.append((mtime, seed["id"], seed))
    candidates.sort(key=lambda item: (item[0], item[1]))
    selected = [item[2] for item in candidates]
    return selected[:limit] if limit else selected


def retry(seed: dict, teacher, judge, max_attempts: int) -> bool:
    """Re-trace and re-screen one rejection; True once a trace is accepted."""
    task_id = seed["id"]
    for attempt in range(1, max_attempts + 1):
        review = current_rejection(task_id)
        feedback = feedback_from_review(review) if review else None
        print(f"[retry] {task_id}: attempt {attempt}/{max_attempts}", flush=True)
        try:
            trace_task(seed, teacher, force=True, feedback=feedback)
            decision = screen(seed, judge)
        except ModelUnavailable:
            raise
        except Exception as error:  # noqa: BLE001 - isolate one seed's failure
            print(f"[retry ERR] {task_id}: {type(error).__name__}: {error}",
                  file=sys.stderr, flush=True)
            continue
        print(f"[{decision.get('status')}] {task_id}: retry {attempt}/{max_attempts}",
              flush=True)
        if is_accepted(decision):
            return True
    # Touch the review so a repeatedly-failing seed rotates behind fresher work.
    review_path = REVIEWS / f"{task_id}.json"
    if review_path.exists():
        review_path.touch()
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0,
                        help="Process at most N oldest rejections (0 = all)")
    args = parser.parse_args(argv)
    if args.max_attempts < 1:
        parser.error("--max-attempts must be at least 1")
    if args.limit < 0:
        parser.error("--limit cannot be negative")

    teacher = get_teacher()
    judge = get_judge()
    require_available(teacher.name)
    require_available(judge.name)
    teacher.preflight(require_auth=True)
    judge.preflight(require_auth=True)

    rejected = rejected_seeds(load_seeds(include_holdout=False), args.limit)
    if not rejected:
        print("quality retry: no current rejected traces")
        return 0
    print(f"quality retry: {len(rejected)} rejected trace(s)")
    unresolved = [seed["id"] for seed in rejected
                  if not retry(seed, teacher, judge, args.max_attempts)]
    if unresolved:
        raise SystemExit("quality retry exhausted for: " + ", ".join(unresolved))
    print(f"quality retry: accepted {len(rejected)}/{len(rejected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
