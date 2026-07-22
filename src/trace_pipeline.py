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
import hashlib
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager

from common import CONFIG, TRACES, select_seeds
from review_contract import is_accepted, is_judge_error
from generate_traces import trace_task
from run_state import (connect, create_run, finish_attempt, set_job,
                       set_run_status, start_attempt, run_row, job_rows,
                       abandon_claim, claim_job, renew_lease, record_model_call)
from runtimes import get_judge, get_teacher
from screen_traces import feedback_from_review, screen
from reasoning_stepdown import (native_effort, next_reasoning_stage,
                                reasoning_schedule, runtime_for_stage)


def _moonshiner_executable() -> str:
    """Return the installed Moonshiner console beside this Python runtime."""
    executable = Path(sys.executable).parent / "moonshiner"
    if executable.is_file():
        return str(executable)
    resolved = shutil.which("moonshiner")
    if resolved:
        return resolved
    raise FileNotFoundError("the installed moonshiner executable was not found")


def _project_root():
    from configuration import PROJECT_ROOT
    return PROJECT_ROOT


def ensure_publish_queue() -> None:
    """Start the independent accepted-trajectory publisher when configured."""
    if not (CONFIG.get("publish") or {}).get("hf_dataset"):
        return
    project_key = hashlib.sha256(str(_project_root()).encode()).hexdigest()[:12]
    unit = f"moonshiner-publish-{project_key}"
    command = ["systemd-run", "--user", "--collect", f"--unit={unit}",
               f"--property=WorkingDirectory={_project_root()}",
               "--property=Restart=on-failure", "--property=RestartSec=10s",
               f"--setenv=PATH={os.environ.get('PATH', '')}",
               _moonshiner_executable(), "publish-queue-worker"]
    status = subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                             f"{unit}.service"])
    if status.returncode != 0:
        subprocess.run(command, check=True)


def existing_harness_trace(seed_id: str) -> bool:
    """Return true only for a buildable trace emitted by a registered harness."""
    meta_path = TRACES / "meta" / f"{seed_id}.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    from normalize import _BY_FORMAT
    if meta.get("trace_format") not in _BY_FORMAT:
        return False
    raw_path = Path(str(meta.get("raw_path") or ""))
    if not raw_path.is_absolute():
        raw_path = TRACES.parent / raw_path
    return bool(meta.get("passed") and raw_path.is_file())


def _selected(args) -> list[dict]:
    from seed_inventory import accepted_ids
    from common import synthetic_tool_contract
    only = {v.strip() for v in args.only.split(",") if v.strip()} if args.only else None
    categories = set(getattr(args, "category", None) or [])
    tags = set(getattr(args, "tag", None) or [])
    # Catalog membership is the only seed intake gate. Quality decisions belong
    # to the trace judge, and lifetime exhaustion prevents paid retry loops.
    ledger = connect()
    accepted = accepted_ids(ledger)
    from run_state import trace_attempt_counts_for_current_seed_revision
    attempts = trace_attempt_counts_for_current_seed_revision(ledger)
    blocked = {str(row[0]) for row in ledger.execute("""
        SELECT latest.seed_id FROM (
          SELECT j.seed_id,j.status,
                 j.updated_at,
                 ROW_NUMBER() OVER (PARTITION BY j.seed_id
                                   ORDER BY j.updated_at DESC,r.created_at DESC) AS rank
          FROM jobs j JOIN runs r ON r.id=j.run_id WHERE r.kind='trace'
        ) AS latest WHERE latest.rank=1 AND latest.status='infrastructure_blocked'
          AND NOT EXISTS (SELECT 1 FROM attempts sa
            JOIN runs sr ON sr.id=sa.run_id WHERE sr.kind='seed'
            AND sa.status='accepted' AND sa.seed_id=latest.seed_id
            AND sa.finished_at>=latest.updated_at)""")}
    maximum = int(getattr(args, "max_attempts", 3))
    trace_config = (CONFIG.get("pipeline", {}).get("trace") or {})
    stepdown = bool(trace_config.get("step_down_reasoning_on_failure", True))
    configured_effort = str((CONFIG.get("teacher") or {}).get("reasoning") or "max")
    required = reasoning_schedule(maximum, stepdown, configured_effort)
    from run_state import trace_reasoning_efforts_for_current_seed_revisions
    selected = select_seeds(only=only, categories=categories, tags=tags,
                            name=getattr(args, "name", None))
    histories = trace_reasoning_efforts_for_current_seed_revisions(
        ledger, {seed["id"] for seed in selected}) if stepdown else {}

    def has_remaining(seed_id: str) -> bool:
        if not stepdown:
            return attempts.get(seed_id, 0) < maximum
        completed = histories.get(seed_id, [])
        return next_reasoning_stage(required, completed) is not None

    seeds = [seed for seed in selected
             if synthetic_tool_contract(seed) is None
             and seed["id"] not in accepted
             and seed["id"] not in blocked
             and has_remaining(seed["id"])]
    from run_state import pending_trace_queue_entries
    queue_order = {entry["seed_id"]: index for index, entry in enumerate(
        pending_trace_queue_entries(ledger))}
    ledger.close()
    retry_order = str((CONFIG.get("pipeline", {}).get("trace") or {})
                      .get("retry_order", "immediate"))
    if retry_order not in {"immediate", "tail"}:
        raise ValueError("pipeline.trace.retry_order must be immediate or tail")
    if retry_order == "tail":
        seeds.sort(key=lambda seed: attempts.get(seed["id"], 0))
    seeds.sort(key=lambda seed: (seed["id"] not in queue_order,
                                queue_order.get(seed["id"], 0)))
    if args.limit:
        seeds = seeds[:args.limit]
    elif not args.all and not only:
        seeds = seeds[:1]  # safe default: a smoke-sized run
    return seeds


def _run_individual_trace_jobs(seeds: list[dict], args, workers: int) -> int:
    """Continuously keep the configured number of one-seed processes active."""
    project = _project_root()
    environment = dict(os.environ, MOONSHINER_SINGLE_TRACE="1")

    def run_one(seed: dict) -> tuple[str, int]:
        command = [_moonshiner_executable(), "run",
                   "--only", seed["id"], "--max-attempts", str(args.max_attempts),
                   "--yes"]
        return seed["id"], subprocess.run(command, cwd=project, env=environment).returncode

    failures = 0
    completed = 0
    supervised = os.environ.get("MOONSHINER_SUPERVISED") == "1"
    pending = list(seeds)

    def configured_workers() -> int:
        if getattr(args, "workers", 0):
            return args.workers
        from configuration import load_config
        value = int(((load_config().get("pipeline") or {}).get("trace") or {})
                    .get("workers", workers))
        if not 1 <= value <= 64:
            raise ValueError("pipeline.trace.workers must be from 1 through 64")
        return value

    # The pool permits increases without restarting the coordinator. Submission,
    # not pool capacity, enforces the live configured worker count.
    with ThreadPoolExecutor(max_workers=64, thread_name_prefix="trace-job") as pool:
        futures: dict = {}
        while pending or futures or supervised:
            active_ids = {seed_id for seed_id in futures.values()}
            if supervised:
                pending_ids = {seed["id"] for seed in pending}
                for seed in _selected(args):
                    if seed["id"] not in active_ids and seed["id"] not in pending_ids:
                        pending.append(seed)
                        pending_ids.add(seed["id"])
            target = configured_workers()
            while pending and len(futures) < target:
                seed = pending.pop(0)
                future = pool.submit(run_one, seed)
                futures[future] = seed["id"]
            if not futures:
                time.sleep(2)
                continue
            done, _ = wait(set(futures), timeout=2, return_when=FIRST_COMPLETED)
            for future in done:
                futures.pop(future, None)
                seed_id, code = future.result()
                completed += 1
                if code:
                    failures += 1
                    print(f"[trace process failed] {seed_id}", flush=True)
                else:
                    print(f"[trace complete: accepted] {seed_id}", flush=True)
    print(f"trace queue pass complete: {completed - failures} accepted, "
          f"{failures} failed processes, {completed} individual trace jobs", flush=True)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    original_argv = list(argv or [])
    defaults = CONFIG.get("pipeline", {}).get("trace", {})
    stepdown_enabled = bool(defaults.get("step_down_reasoning_on_failure", True))
    parser = argparse.ArgumentParser(
        prog="moonshiner run",
        description="Run the bounded trace quality loop with a durable ledger.")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--all", action="store_true",
                        help="Explicitly authorize every eligible seed.")
    choice.add_argument("--only", help="Comma-separated seed ids.")
    parser.add_argument("--category", action="append",
                        help="Require this catalog category (repeatable).")
    parser.add_argument("--tag", action="append",
                        help="Require this training tag (repeatable; all must match).")
    parser.add_argument("--name", help="Match text in the seed ID or prompt.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Maximum seeds (default 1 unless --all/--only).")
    parser.add_argument("--max-attempts", type=int,
                        default=int(defaults.get("max_attempts", 2)))
    parser.add_argument("--workers", type=int, default=0,
                        help="Trace workers (0 follows live project configuration).")
    parser.add_argument("--yes", action="store_true",
                        help="Confirm a run selecting more than one seed.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--detach", action="store_true",
                        help="Launch the bounded run in a durable background scope.")
    parser.add_argument("--resume", help="Resume pending jobs in an interrupted run.")
    args = parser.parse_args(argv)
    if args.limit < 0 or args.max_attempts < 1 or args.workers < 0:
        parser.error("limits must be non-negative and --max-attempts at least 1")

    coordinator_lock = None
    if not args.detach and os.environ.get("MOONSHINER_SINGLE_TRACE") != "1":
        from common import RUNS
        RUNS.mkdir(parents=True, exist_ok=True)
        coordinator_lock = (RUNS / "trace-coordinator.lock").open("a+")
        try:
            fcntl.flock(coordinator_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("a trace coordinator is already running for this project", file=sys.stderr)
            return 2
    db = connect()
    if args.resume:
        prior=run_row(db,args.resume)
        if not prior: parser.error("resume run id not found")
        ids={j["seed_id"] for j in job_rows(db,args.resume)
             if j["status"] in {"pending","running","retry"}}
        prior_limits=json.loads(prior["limits_json"])
        seeds=select_seeds(only=ids); args.max_attempts=prior_limits["max_attempts"]
        stepdown_enabled = bool(prior_limits.get(
            "step_down_reasoning_on_failure", stepdown_enabled))
    else:
        seeds = _selected(args)
    if not seeds:
        print("no eligible catalog seeds matched", file=sys.stderr)
        return 2
    teacher = get_teacher()
    judge = get_judge()
    if stepdown_enabled:
        for stage in dict.fromkeys(reasoning_schedule(
                args.max_attempts, True, str(teacher.role.get("reasoning") or "max"))):
            runtime_for_stage(teacher, stage)
    print(f"trace plan: {len(seeds)} seed(s), up to {args.max_attempts} attempt(s) "
          f"each; no run-wide model-call ceiling")
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
        stamp = time.strftime("%Y%m%d-%H%M%S")
        unit = f"moonshiner-trace-{stamp}"
        log_dir = _project_root() / ".moonshiner" / "runs" / unit
        log_dir.mkdir(parents=True, exist_ok=True)
        log = log_dir / "run.log"
        child_argv = [value for value in original_argv if value != "--detach"]
        command = ["systemd-run", "--user", "--collect", f"--unit={unit}",
                   f"--property=WorkingDirectory={_project_root()}",
                   "--property=Restart=on-failure", "--property=RestartSec=10s",
                   f"--property=StandardOutput=append:{log}",
                   f"--property=StandardError=append:{log}",
                   f"--setenv=PATH={os.environ.get('PATH', '')}",
                   "--setenv=MOONSHINER_SUPERVISED=1",
                   _moonshiner_executable(), "run", *child_argv]
        result = subprocess.run(command)
        if result.returncode == 0:
            print(f"trace queue started: {unit}")
            print(f"log: {log}")
        return result.returncode
    if len(seeds) > 1 and os.environ.get("MOONSHINER_SINGLE_TRACE") != "1":
        workers = args.workers or int(defaults.get("workers", 1))
        return _run_individual_trace_jobs(seeds, args, workers)

    # A dry run never touches the network or local dataset. The first real run
    # bootstraps the configured HF canonical only when it is locally absent.
    from hf_sync import ensure_local_dataset
    sync = ensure_local_dataset()
    if sync.get("status") not in {"unconfigured", "local_append"}:
        print(f"HF local dataset: {sync.get('status')} ({sync.get('origin', 'existing')})")

    teacher.preflight(require_auth=True)
    judge.preflight(require_auth=True)
    ensure_publish_queue()
    limits = {"seeds": len(seeds), "max_attempts": args.max_attempts}
    limits["step_down_reasoning_on_failure"] = stepdown_enabled
    retry_order = str(defaults.get("retry_order", "immediate"))
    if retry_order not in {"immediate", "tail"}:
        raise ValueError("pipeline.trace.retry_order must be immediate or tail")
    roles = {"author": {"runtime": teacher.name, **teacher.role},
             "judge": {"runtime": judge.name, **judge.role}}
    run_id = args.resume or create_run(db, "trace", roles, limits, [s["id"] for s in seeds])
    if not args.resume:
        # Carry attempts for the current authored seed revision into its new
        # one-seed ledger record. Reauthoring deliberately starts a fresh trace
        # lifecycle; older trace attempts belong to the superseded seed.
        from run_state import trace_attempt_counts_for_current_seed_revision
        prior_counts = trace_attempt_counts_for_current_seed_revision(db)
        for seed in seeds:
            db.execute("UPDATE jobs SET attempts=? WHERE run_id=? AND seed_id=?",
                       (prior_counts.get(seed["id"], 0), run_id, seed["id"]))
        db.commit()
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
        configured_effort = str(worker_teacher.role.get("reasoning") or "max")
        required_stages = reasoning_schedule(args.max_attempts, stepdown_enabled,
                                             configured_effort)
        from run_state import trace_reasoning_efforts_for_current_seed_revision
        completed_stages = trace_reasoning_efforts_for_current_seed_revision(
            worker_db, seed["id"])
        stage = (next_reasoning_stage(required_stages, completed_stages)
                 if stepdown_enabled else configured_effort)
        if stage is None or (not stepdown_enabled and number > args.max_attempts):
            set_job(worker_db, run_id, seed["id"], "exhausted", claim["attempts"],
                    claim.get("last_error") or "attempt ceiling reached")
            return
        effort = native_effort(worker_teacher.name, stage)
        attempt_teacher = (runtime_for_stage(worker_teacher, stage)
                           if stepdown_enabled else worker_teacher)
        has_more = next_reasoning_stage(required_stages,
                                        [*completed_stages, stage]) is not None
        from common import preflight_seed_environment, synthetic_tool_contract
        synthetic = synthetic_tool_contract(seed)
        if synthetic:
            set_job(worker_db, run_id, seed["id"], "infrastructure_blocked",
                    claim["attempts"], synthetic)
            print(f"[infrastructure blocked] {seed['id']}: {synthetic}", flush=True)
            return
        environment_ok, environment_detail = preflight_seed_environment(seed)
        if not environment_ok:
            set_job(worker_db, run_id, seed["id"], "infrastructure_blocked",
                    claim["attempts"], environment_detail)
            print(f"[infrastructure blocked] {seed['id']}: {environment_detail}",
                  flush=True)
            return
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
            if claim["attempts"] == 0 and existing_harness_trace(seed["id"]):
                start_attempt(worker_db, run_id, seed["id"], number,
                              reasoning_stage=stage, reasoning_effort=effort)
                meta_path = TRACES / "meta" / f"{seed['id']}.json"
                meta = json.loads(meta_path.read_text())
                meta.setdefault("teacher", {})["reasoning_stage"] = stage
                meta_path.write_text(json.dumps(meta, indent=2) + "\n")
                record_model_call(worker_db, run_id)
                print(f"[{seed['id']}] existing harness trace: judge", flush=True)
                review = screen(seed, worker_judge)
                if is_judge_error(review):
                    reason = str(review.get("reason") or "judge execution failed")
                    finish_attempt(worker_db, run_id, seed["id"], number,
                                   "infrastructure_error", review=review, error=reason)
                    set_job(worker_db, run_id, seed["id"], "infrastructure_blocked",
                            claim["attempts"], reason)
                    return
                if is_accepted(review):
                    artifact = _archive_attempt(run_id, seed["id"], number)
                    finish_attempt(worker_db, run_id, seed["id"], number, "accepted",
                                   review=review, artifact_path=artifact)
                    print(f"[accepted existing trace] {seed['id']}", flush=True)
                    return
                artifact = _archive_attempt(run_id, seed["id"], number)
                reason = feedback_from_review(review)
                finish_attempt(worker_db, run_id, seed["id"], number,
                               "retry" if has_more else "exhausted",
                               review=review, error=reason, artifact_path=artifact)
                if has_more and retry_order == "tail":
                    set_job(worker_db, run_id, seed["id"], "deferred", number, reason)
                print(f"[rejected existing trace] {seed['id']}: {reason}", flush=True)
                return
            record_model_call(worker_db, run_id)
            start_attempt(worker_db, run_id, seed["id"], number,
                          reasoning_stage=stage, reasoning_effort=effort)
            print(f"[{seed['id']}] attempt {number} ({stage}): author", flush=True)
            record = trace_task(seed, attempt_teacher, force=True,
                                reasoning_stage=stage)
        usage = (record.get("teacher") or {}).get("usage") or {}
        # Candidate checks are evidence for the trace judge, never a separate
        # rejection gate. Every completed candidate proceeds to judgment.
        record_model_call(worker_db, run_id)
        print(f"[{seed['id']}] attempt {number} ({stage}): judge", flush=True)
        with lease_heartbeat():
            review = screen(seed, worker_judge)
        if is_judge_error(review):
            artifact = _archive_attempt(run_id, seed["id"], number)
            reason = str(review.get("reason") or "judge execution failed")
            finish_attempt(worker_db, run_id, seed["id"], number,
                           "infrastructure_error", usage, review, reason,
                           artifact_path=artifact)
            set_job(worker_db, run_id, seed["id"], "infrastructure_blocked",
                    claim["attempts"], reason)
            print(f"[infrastructure blocked] {seed['id']}: {reason}", flush=True)
            return
        if is_accepted(review):
            artifact = _archive_attempt(run_id, seed["id"], number)
            finish_attempt(worker_db, run_id, seed["id"], number, "accepted",
                           usage, review, artifact_path=artifact)
            print(f"[accepted] {seed['id']}", flush=True)
            return
        status = "retry" if has_more else "exhausted"
        artifact = _archive_attempt(run_id, seed["id"], number)
        reason = feedback_from_review(review)
        finish_attempt(worker_db, run_id, seed["id"], number, status, usage,
                       review, reason, artifact_path=artifact)
        if status == "retry" and retry_order == "tail":
            set_job(worker_db, run_id, seed["id"], "deferred", number, reason)
            status = "deferred"
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
    deferred = sum(row["status"] == "deferred" for row in rows)
    print(f"trace run complete: {accepted}/{total_jobs} accepted; {calls} model calls")
    print(f"inspect: moonshiner inspect {run_id}")
    return 0 if accepted + deferred == total_jobs else 1

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
