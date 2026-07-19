"""Bounded, durable generate → judge → retrace pipeline."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from common import CONFIG, load_seeds, quarantined_tasks
from generate_traces import trace_task
from run_state import (connect, create_run, finish_attempt, set_job,
                       set_run_status, start_attempt)
from runtimes import get_judge, get_teacher
from screen_traces import feedback_from_review, screen


def _selected(args) -> list[dict]:
    only = {v.strip() for v in args.only.split(",") if v.strip()} if args.only else None
    seeds = [s for s in load_seeds(only=only) if s["id"] not in quarantined_tasks()]
    if args.limit:
        seeds = seeds[:args.limit]
    elif not args.all and not only:
        seeds = seeds[:1]  # safe default: a smoke-sized run
    return seeds


def main(argv: list[str] | None = None) -> int:
    original_argv = list(argv or [])
    defaults = CONFIG.get("pipeline", {}).get("trace", {})
    parser = argparse.ArgumentParser(
        description="Run the bounded trace quality loop with a durable ledger.")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--all", action="store_true",
                        help="Explicitly authorize every eligible seed.")
    choice.add_argument("--only", help="Comma-separated seed ids.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Maximum seeds (default 1 unless --all/--only).")
    parser.add_argument("--max-attempts", type=int,
                        default=int(defaults.get("max_attempts", 2)))
    parser.add_argument("--max-calls", type=int, default=0,
                        help="Maximum combined teacher+judge calls (0 derives from plan).")
    parser.add_argument("--yes", action="store_true",
                        help="Confirm a run selecting more than one seed.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--detach", action="store_true",
                        help="Launch the bounded run in a durable background scope.")
    args = parser.parse_args(argv)
    if args.limit < 0 or args.max_attempts < 1 or args.max_calls < 0:
        parser.error("limits must be non-negative and --max-attempts at least 1")

    seeds = _selected(args)
    if not seeds:
        print("no seeds matched", file=sys.stderr)
        return 2
    planned_calls = len(seeds) * args.max_attempts * 2
    max_calls = args.max_calls or planned_calls
    teacher = get_teacher()
    judge = get_judge()
    print(f"trace plan: {len(seeds)} seed(s), up to {args.max_attempts} attempt(s) "
          f"each, hard ceiling {max_calls} model call(s)")
    print(f"  author: {teacher.name}/{teacher.role['model']} "
          f"({teacher.role.get('reasoning', 'default')})")
    print(f"  judge:  {judge.name}/{judge.role['model']} "
          f"({judge.role.get('reasoning', 'default')})")
    if args.dry_run:
        for seed in seeds:
            print(f"  - {seed['id']}")
        return 0
    if len(seeds) > 1 and not args.yes:
        print("refusing a multi-seed metered run without --yes", file=sys.stderr)
        return 2
    if args.detach:
        command = [str(__import__('common').ROOT / "scripts" / "batch.sh"),
                   "trace", sys.executable, str(__import__('common').ROOT / "moonshiner.py"),
                   "run", *[value for value in original_argv if value != "--detach"]]
        return subprocess.run(command).returncode

    teacher.preflight(require_auth=True)
    judge.preflight(require_auth=True)
    db = connect()
    limits = {"seeds": len(seeds), "max_attempts": args.max_attempts,
              "max_calls": max_calls}
    roles = {"author": {"runtime": teacher.name, **teacher.role},
             "judge": {"runtime": judge.name, **judge.role}}
    run_id = create_run(db, "trace", roles, limits, [s["id"] for s in seeds])
    print(f"run: {run_id}", flush=True)

    calls = accepted = 0
    try:
        for seed in seeds:
            feedback = None
            done = False
            for number in range(1, args.max_attempts + 1):
                if calls >= max_calls:
                    set_job(db, run_id, seed["id"], "exhausted", number - 1,
                            "model-call ceiling reached")
                    break
                start_attempt(db, run_id, seed["id"], number)
                print(f"[{seed['id']}] attempt {number}/{args.max_attempts}: author",
                      flush=True)
                record = trace_task(seed, teacher, force=True, feedback=feedback)
                calls += 1
                usage = (record.get("teacher") or {}).get("usage") or {}
                if record.get("passed") is not True:
                    error = record.get("deferral_reason") or record.get("verify_output") \
                        or "candidate failed local verification"
                    finish_attempt(db, run_id, seed["id"], number, "retry", usage,
                                   error=str(error)[:1000])
                    feedback = f"The prior candidate failed local verification: {error}"
                    continue
                if calls >= max_calls:
                    finish_attempt(db, run_id, seed["id"], number, "exhausted", usage,
                                   error="model-call ceiling reached before judgment")
                    break
                # Judge transport/schema faults re-review the same trace. Only a
                # substantive rejection is allowed to spend another author call.
                while True:
                    print(f"[{seed['id']}] attempt {number}/{args.max_attempts}: judge",
                          flush=True)
                    review = screen(seed, judge)
                    calls += 1
                    if review.get("status") != "judge_error" or calls >= max_calls:
                        break
                if review.get("accepted") is True:
                    finish_attempt(db, run_id, seed["id"], number, "accepted",
                                   usage, review)
                    accepted += 1
                    done = True
                    print(f"[accepted] {seed['id']}", flush=True)
                    break
                feedback = feedback_from_review(review)
                status = "retry" if number < args.max_attempts else "exhausted"
                finish_attempt(db, run_id, seed["id"], number, status, usage,
                               review, review.get("reason"))
                print(f"[{status}] {seed['id']}: {review.get('reason', '')}", flush=True)
            if not done:
                # The last attempt already records the terminal explanation.
                pass
        status = "complete" if accepted == len(seeds) else "complete_with_rejections"
        set_run_status(db, run_id, status)
    except KeyboardInterrupt:
        set_run_status(db, run_id, "interrupted", "keyboard interrupt")
        return 130
    except Exception as error:  # infrastructure failure, unlike a candidate rejection
        set_run_status(db, run_id, "failed", f"{type(error).__name__}: {error}")
        raise
    print(f"trace run complete: {accepted}/{len(seeds)} accepted; {calls}/{max_calls} calls")
    print(f"inspect: python3 moonshiner.py inspect {run_id}")
    return 0 if accepted == len(seeds) else 1
