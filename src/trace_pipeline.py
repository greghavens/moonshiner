"""Bounded, durable generate → judge → retrace pipeline."""
from __future__ import annotations

import argparse, shutil
import json
import subprocess
import sys
import os
import threading
import time
import uuid
import fcntl
from contextlib import contextmanager

from common import CONFIG, load_seeds, quarantined_tasks, select_seeds
from generate_traces import trace_task
from run_state import (connect, create_run, finish_attempt, set_job,
                       set_run_status, start_attempt, run_row, job_rows,
                       abandon_claim, claim_job, renew_lease, reserve_model_call)
from run_state import latest_running_run
from runtimes import get_judge, get_teacher
from screen_traces import feedback_from_review, screen


def ensure_publish_queue() -> None:
    """Start the independent accepted-trajectory publisher when configured."""
    if not (CONFIG.get("publish") or {}).get("hf_dataset"):
        return
    unit = "moonshiner-publish-queue"
    command = ["systemd-run", "--user", "--collect", f"--unit={unit}",
               f"--property=WorkingDirectory={__import__('common').ROOT}",
               "--property=Restart=on-failure", "--property=RestartSec=10s",
               f"--setenv=PATH={os.environ.get('PATH', '')}",
               sys.executable, str(__import__('common').ROOT / "src" / "publish_queue.py")]
    status = subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                             f"{unit}.service"])
    if status.returncode != 0:
        subprocess.run(command, check=True)


def _selected(args) -> list[dict]:
    from import_existing import imported_task_ids
    only = {v.strip() for v in args.only.split(",") if v.strip()} if args.only else None
    imported = imported_task_ids()
    from common import TRACES
    accepted=set()
    for path in (TRACES/"reviews").glob("behavior-*.json"):
        try: review=json.loads(path.read_text())
        except (OSError,json.JSONDecodeError): continue
        if (review.get("accepted") is True
                and (review.get("deterministic") or {}).get("accepted") is True
                and (review.get("judge") or {}).get("model_attested") is True):
            accepted.add(path.stem)
    categories = set(getattr(args, "category", None) or [])
    tags = set(getattr(args, "tag", None) or [])
    selected_kind=getattr(args, "kind", "coding")
    if only and any(value.startswith("behavior-") for value in only):
        selected_kind="all"
    seeds = [s for s in select_seeds(kind=selected_kind, only=only,
                                     categories=categories, tags=tags,
                                     name=getattr(args, "name", None),
                                     require_authored=True)
             if s["id"] not in quarantined_tasks() and s["id"] not in imported
             and s["id"] not in accepted]
    intake_only = bool((CONFIG.get("pipeline", {}).get("trace", {})
                        .get("accepted_seed_intake_only")))
    if intake_only and args.all and not only and selected_kind in {"coding", "all"}:
        marker_value = (CONFIG.get("source") or {}).get("accepted_seed_markers")
        if not marker_value:
            raise ValueError("accepted-only tracing requires source.accepted_seed_markers")
        from pathlib import Path
        from seed_intake import accepted_seed_ids
        marker_dir = Path(marker_value).expanduser()
        if not marker_dir.is_absolute():
            marker_dir = (__import__('common').ROOT / marker_dir).resolve()
        allowed = accepted_seed_ids(marker_dir)
        seeds = [seed for seed in seeds if seed["id"] in allowed]
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
    parser.add_argument("--kind", choices=["coding", "behavior", "all"],
                        default="coding", help="Seed recipe kind (default: coding).")
    parser.add_argument("--category", action="append",
                        help="Require this catalog category (repeatable).")
    parser.add_argument("--tag", action="append",
                        help="Require this training tag (repeatable; all must match).")
    parser.add_argument("--name", help="Match text in the seed ID or prompt.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Maximum seeds (default 1 unless --all/--only).")
    parser.add_argument("--max-attempts", type=int,
                        default=int(defaults.get("max_attempts", 2)))
    parser.add_argument("--max-calls", type=int, default=0,
                        help="Maximum combined teacher+judge calls (0 derives from plan).")
    parser.add_argument("--workers", type=int, default=0,
                        help="Trace workers (0 follows live project configuration).")
    parser.add_argument("--yes", action="store_true",
                        help="Confirm a run selecting more than one seed.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--detach", action="store_true",
                        help="Launch the bounded run in a durable background scope.")
    parser.add_argument("--resume", help="Resume pending jobs in an interrupted run.")
    args = parser.parse_args(argv)
    if args.limit < 0 or args.max_attempts < 1 or args.max_calls < 0 or args.workers < 0:
        parser.error("limits must be non-negative and --max-attempts at least 1")

    coordinator_lock = None
    if not args.detach:
        from common import RUNS
        RUNS.mkdir(parents=True, exist_ok=True)
        coordinator_lock = (RUNS / "trace-coordinator.lock").open("a+")
        try:
            fcntl.flock(coordinator_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("a trace coordinator is already running for this project", file=sys.stderr)
            return 2
    db = connect()
    if not args.resume and os.environ.get("MOONSHINER_SUPERVISED") == "1":
        active = latest_running_run(db, "trace")
        if active:
            args.resume = active["id"]
            print(f"recovering supervised trace run {args.resume}", flush=True)
    if args.resume:
        prior=run_row(db,args.resume)
        if not prior: parser.error("resume run id not found")
        ids={j["seed_id"] for j in job_rows(db,args.resume)
             if j["status"] in {"pending","running","retry"}}
        prior_limits=json.loads(prior["limits_json"])
        seeds=select_seeds(kind="all", only=ids, require_authored=True); args.max_attempts=prior_limits["max_attempts"]
        args.max_calls=prior_limits["max_calls"]
    else:
        seeds = _selected(args)
    if not seeds:
        if getattr(args, "kind", "coding") == "behavior":
            from common import load_behavior_seeds
            total=len(load_behavior_seeds())
            authored=len(load_behavior_seeds(authored_only=True))
            print(f"no authored behavior seeds matched (authored {authored}/{total}); "
                  "finish `moonshiner behavior-seed author --all --yes` first",
                  file=sys.stderr)
        else:
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
    configured_workers = args.workers or int(defaults.get("workers", 1))
    print(f"  trace workers: {configured_workers}"
          + (" (fixed for this run)" if args.workers else " (live-configurable)"))
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

    # A dry run never touches the network or local dataset. The first real run
    # bootstraps the configured HF canonical only when it is locally absent.
    from hf_sync import ensure_local_dataset
    sync = ensure_local_dataset()
    if sync.get("status") not in {"unconfigured", "local_append"}:
        print(f"HF local dataset: {sync.get('status')} ({sync.get('origin', 'existing')})")

    if all(seed.get("kind") == "tool_behavior" for seed in seeds):
        from runtimes.auth import load_provider_key
        load_provider_key(teacher.runtime_config)
    else:
        teacher.preflight(require_auth=True)
    judge.preflight(require_auth=True)
    ensure_publish_queue()
    limits = {"seeds": len(seeds), "max_attempts": args.max_attempts,
              "max_calls": max_calls}
    roles = {"author": {"runtime": teacher.name, **teacher.role},
             "judge": {"runtime": judge.name, **judge.role}}
    run_id = args.resume or create_run(db, "trace", roles, limits, [s["id"] for s in seeds])
    if args.resume: set_run_status(db,run_id,"running")
    total_jobs = len(job_rows(db, run_id))
    print(f"run: {run_id}", flush=True)

    seed_by_id = {seed["id"]: seed for seed in seeds}
    worker_errors: list[BaseException] = []
    error_lock = threading.Lock()
    stop_claiming = threading.Event()

    def desired_workers() -> int:
        if args.workers:
            return args.workers
        from configuration import load_config
        value = int(((load_config().get("pipeline") or {}).get("trace") or {})
                    .get("workers", 1))
        if not 1 <= value <= 64:
            raise ValueError("pipeline.trace.workers must be from 1 through 64")
        return value

    def process_claim(worker_db, owner: str, claim: dict, worker_teacher, worker_judge):
        seed = seed_by_id[claim["seed_id"]]
        number = claim["attempts"] + 1
        if number > args.max_attempts:
            set_job(worker_db, run_id, seed["id"], "exhausted", claim["attempts"],
                    claim.get("last_error") or "attempt ceiling reached")
            return
        if reserve_model_call(worker_db, run_id, max_calls) is None:
            set_job(worker_db, run_id, seed["id"], "exhausted", claim["attempts"],
                    "model-call ceiling reached")
            return
        start_attempt(worker_db, run_id, seed["id"], number)
        feedback = claim.get("last_error")
        print(f"[{seed['id']}] attempt {number}/{args.max_attempts}: author", flush=True)
        @contextmanager
        def lease_heartbeat():
            stopped = threading.Event()
            def heartbeat():
                lease_db = connect()
                try:
                    while not stopped.wait(30):
                        if not renew_lease(lease_db, run_id, seed["id"], owner):
                            return
                finally:
                    lease_db.close()
            renew_lease(worker_db, run_id, seed["id"], owner)
            thread = threading.Thread(target=heartbeat, name=f"lease-{seed['id']}",
                                      daemon=True)
            thread.start()
            try:
                yield
            finally:
                stopped.set(); thread.join()

        with lease_heartbeat():
            if seed.get("kind") == "tool_behavior":
                from behavior_trace import trace_task as behavior_trace_task
                record = behavior_trace_task(seed, worker_teacher, feedback=feedback)
            else:
                record = trace_task(seed, worker_teacher, force=True, feedback=feedback)
        usage = (record.get("teacher") or {}).get("usage") or {}
        if record.get("passed") is not True:
            error = record.get("deferral_reason") or record.get("verify_output") \
                or "candidate failed local verification"
            status = "retry" if number < args.max_attempts else "exhausted"
            artifact = _archive_attempt(run_id, seed["id"], number)
            finish_attempt(worker_db, run_id, seed["id"], number, status, usage,
                           error=str(error)[:1000], artifact_path=artifact)
            print(f"[{status}] {seed['id']}: {str(error)[:500]}", flush=True)
            return
        review = None
        while True:
            if reserve_model_call(worker_db, run_id, max_calls) is None:
                artifact = _archive_attempt(run_id, seed["id"], number)
                finish_attempt(worker_db, run_id, seed["id"], number, "exhausted", usage,
                               review, "model-call ceiling reached before judgment",
                               artifact_path=artifact)
                return
            print(f"[{seed['id']}] attempt {number}/{args.max_attempts}: judge", flush=True)
            with lease_heartbeat():
                if seed.get("kind") == "tool_behavior":
                    from behavior_trace import judge_trace
                    review = judge_trace(seed, worker_judge)
                else:
                    review = screen(seed, worker_judge)
            if review.get("status") != "judge_error":
                break
        if review.get("accepted") is True:
            artifact = _archive_attempt(run_id, seed["id"], number)
            finish_attempt(worker_db, run_id, seed["id"], number, "accepted",
                           usage, review, artifact_path=artifact)
            print(f"[accepted] {seed['id']}", flush=True)
            return
        status = "retry" if number < args.max_attempts else "exhausted"
        artifact = _archive_attempt(run_id, seed["id"], number)
        reason = feedback_from_review(review)
        finish_attempt(worker_db, run_id, seed["id"], number, status, usage,
                       review, reason, artifact_path=artifact)
        print(f"[{status}] {seed['id']}: {reason}", flush=True)

    def worker(index: int):
        owner = f"{run_id}:worker-{index}:{uuid.uuid4().hex[:8]}"
        worker_db = connect()
        worker_teacher, worker_judge = get_teacher(), get_judge()
        claim = None
        try:
            while not stop_claiming.is_set():
                if index >= desired_workers():
                    return
                claim = claim_job(worker_db, run_id, owner)
                if claim is None:
                    return
                process_claim(worker_db, owner, claim, worker_teacher, worker_judge)
        except BaseException as error:
            if claim is not None:
                abandon_claim(worker_db, run_id, claim["seed_id"], owner,
                              f"{type(error).__name__}: {error}")
            with error_lock:
                worker_errors.append(error)
            stop_claiming.set()
        finally:
            worker_db.close()

    threads: dict[int, threading.Thread] = {}
    try:
        while True:
            if worker_errors:
                raise worker_errors[0]
            rows = job_rows(db, run_id)
            unfinished = [row for row in rows if row["status"] in {"pending", "retry", "running"}]
            if not unfinished:
                break
            target = desired_workers()
            for index in range(target):
                thread = threads.get(index)
                if thread is None or not thread.is_alive():
                    thread = threading.Thread(target=worker, args=(index,),
                                              name=f"trace-worker-{index}", daemon=False)
                    threads[index] = thread; thread.start()
            time.sleep(1)
        for thread in threads.values():
            thread.join()
        rows = job_rows(db, run_id)
        accepted = sum(row["status"] == "accepted" for row in rows)
        failed = sum(row["status"] in {"exhausted", "failed"} for row in rows)
        set_run_status(db, run_id, "complete" if not failed else "complete_with_rejections")
    except KeyboardInterrupt:
        stop_claiming.set()
        for thread in threads.values(): thread.join()
        set_run_status(db, run_id, "interrupted", "keyboard interrupt")
        return 130
    except BaseException as error:
        stop_claiming.set()
        for thread in threads.values(): thread.join()
        set_run_status(db, run_id, "failed", f"{type(error).__name__}: {error}")
        raise
    calls = int((run_row(db, run_id) or {}).get("model_calls") or 0)
    print(f"trace run complete: {accepted}/{total_jobs} accepted; {calls}/{max_calls} calls")
    print(f"inspect: moonshiner inspect {run_id}")
    return 0 if accepted == total_jobs else 1

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
