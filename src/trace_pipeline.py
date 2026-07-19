"""Bounded, durable generate → judge → retrace pipeline."""
from __future__ import annotations

import argparse, shutil
import json
import subprocess
import sys

from common import CONFIG, load_seeds, quarantined_tasks
from generate_traces import trace_task
from run_state import (connect, create_run, finish_attempt, set_job,
                       set_run_status, start_attempt, run_row, job_rows,
                       record_model_call)
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
        prog="moonshiner run",
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
    parser.add_argument("--resume", help="Resume pending jobs in an interrupted run.")
    args = parser.parse_args(argv)
    if args.limit < 0 or args.max_attempts < 1 or args.max_calls < 0:
        parser.error("limits must be non-negative and --max-attempts at least 1")

    db = connect()
    if args.resume:
        prior=run_row(db,args.resume)
        if not prior: parser.error("resume run id not found")
        ids={j["seed_id"] for j in job_rows(db,args.resume)
             if j["status"] in {"pending","running","retry"}}
        prior_limits=json.loads(prior["limits_json"])
        seeds=load_seeds(only=ids); args.max_attempts=prior_limits["max_attempts"]
        args.max_calls=prior_limits["max_calls"]
    else:
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
    limits = {"seeds": len(seeds), "max_attempts": args.max_attempts,
              "max_calls": max_calls}
    roles = {"author": {"runtime": teacher.name, **teacher.role},
             "judge": {"runtime": judge.name, **judge.role}}
    run_id = args.resume or create_run(db, "trace", roles, limits, [s["id"] for s in seeds])
    if args.resume: set_run_status(db,run_id,"running")
    print(f"run: {run_id}", flush=True)

    calls = int((run_row(db, run_id) or {}).get("model_calls") or 0)
    accepted = 0
    try:
        for seed in seeds:
            feedback = None
            done = False
            existing=next((j["attempts"] for j in job_rows(db,run_id) if j["seed_id"]==seed["id"]),0)
            for number in range(existing+1, args.max_attempts + 1):
                if calls >= max_calls:
                    set_job(db, run_id, seed["id"], "exhausted", number - 1,
                            "model-call ceiling reached")
                    break
                start_attempt(db, run_id, seed["id"], number)
                print(f"[{seed['id']}] attempt {number}/{args.max_attempts}: author",
                      flush=True)
                calls = record_model_call(db, run_id)
                record = trace_task(seed, teacher, force=True, feedback=feedback)
                usage = (record.get("teacher") or {}).get("usage") or {}
                if record.get("passed") is not True:
                    error = record.get("deferral_reason") or record.get("verify_output") \
                        or "candidate failed local verification"
                    artifact=_archive_attempt(run_id,seed["id"],number)
                    finish_attempt(db, run_id, seed["id"], number, "retry", usage,
                                   error=str(error)[:1000],artifact_path=artifact)
                    feedback = f"The prior candidate failed local verification: {error}"
                    continue
                if calls >= max_calls:
                    artifact=_archive_attempt(run_id,seed["id"],number)
                    finish_attempt(db, run_id, seed["id"], number, "exhausted", usage,
                                   error="model-call ceiling reached before judgment",artifact_path=artifact)
                    break
                # Judge transport/schema faults re-review the same trace. Only a
                # substantive rejection is allowed to spend another author call.
                while True:
                    print(f"[{seed['id']}] attempt {number}/{args.max_attempts}: judge",
                          flush=True)
                    calls = record_model_call(db, run_id)
                    review = screen(seed, judge)
                    if review.get("status") != "judge_error" or calls >= max_calls:
                        break
                if review.get("status") == "judge_error":
                    artifact=_archive_attempt(run_id,seed["id"],number)
                    finish_attempt(db, run_id, seed["id"], number, "exhausted",
                                   usage, review,
                                   "judge unavailable or unattested; trace retained",
                                   artifact_path=artifact)
                    break
                if review.get("accepted") is True:
                    artifact=_archive_attempt(run_id,seed["id"],number)
                    finish_attempt(db, run_id, seed["id"], number, "accepted",
                                   usage, review,artifact_path=artifact)
                    accepted += 1
                    done = True
                    print(f"[accepted] {seed['id']}", flush=True)
                    break
                feedback = feedback_from_review(review)
                status = "retry" if number < args.max_attempts else "exhausted"
                artifact=_archive_attempt(run_id,seed["id"],number)
                finish_attempt(db, run_id, seed["id"], number, status, usage,
                               review, review.get("reason"),artifact_path=artifact)
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
    print(f"inspect: moonshiner inspect {run_id}")
    return 0 if accepted == len(seeds) else 1

def _archive_attempt(run_id: str, seed_id: str, number: int) -> str:
    from common import RUNS, TRACES
    out=RUNS/run_id/"artifacts"/seed_id/f"attempt-{number:04d}"; out.mkdir(parents=True,exist_ok=True)
    for directory,suffix in (("meta",".json"),("diffs",".patch"),("reviews",".json")):
        source=TRACES/directory/f"{seed_id}{suffix}"
        if source.exists(): shutil.copy2(source,out/f"{directory}{suffix}")
    meta=TRACES/"meta"/f"{seed_id}.json"
    if meta.exists():
        record=json.loads(meta.read_text()); raw=TRACES.parent/record.get("raw_path","")
        if raw.is_file(): shutil.copy2(raw,out/raw.name)
    return str(out)
