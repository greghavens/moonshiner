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

from common import CONFIG, TRACES, WORKSPACES, select_seeds
from review_contract import is_accepted, is_judge_error
from generate_traces import trace_task
from run_state import (connect, create_run, finish_attempt, set_job,
                       set_run_status, start_attempt, run_row, job_rows,
                       abandon_claim, claim_job, renew_lease, record_model_call)
from runtimes import (NoCompatibleTraceHarness,
                      TraceHarnessInfrastructureFailure, get_judge,
                      get_teacher, resolve_trace_harness)
from runtimes.availability import (INFRASTRUCTURE_EXIT, USAGE_LIMIT_EXIT,
                                   ModelUnavailable)
from screen_traces import (JUDGE_ERROR_LIMIT, feedback_from_review, screen,
                           unjudged_review)
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
    if status.returncode == 0:
        return
    # Two trace processes reach this at once on any multi-worker pass, and
    # `is-active` is still false for a unit that is merely deactivating, so
    # the loser of the race gets "unit was already loaded" — a report that the
    # publisher is running, not that anything is wrong. Under `check=True`
    # that raised out of `main`, and every non-zero trace exit is mapped to
    # INFRASTRUCTURE_EXIT, which clears the pending queue and is one of the
    # codes `RestartPreventExitStatus` refuses to restart: losing a race to
    # start the publisher permanently stopped tracing. Publishing is
    # downstream of tracing and does not get to halt it. The next pass calls
    # this again, and accepted traces wait in the ledger meanwhile.
    started = subprocess.run(command, capture_output=True, text=True)
    if started.returncode != 0:
        detail = (started.stderr or started.stdout or "").strip().splitlines()
        print(f"publish queue not started ({unit}), tracing continues: "
              f"{detail[-1] if detail else 'no detail reported'}", flush=True)


def remove_completed_workspace(record: dict) -> None:
    """Remove the materialized workspace after durable attempt completion."""
    value = record.get("_workspace_path")
    if not value:
        return
    from common import remove_workspace
    remove_workspace(Path(str(value)), workspaces=WORKSPACES)


class InfrastructureFailure(RuntimeError):
    """The environment is broken. Not this seed's fault, and not survivable."""


def alert_infrastructure_failure(seed_id: str, reason: str) -> None:
    banner = "=" * 72
    print(f"\n{banner}\n[INFRASTRUCTURE FAILURE] {seed_id}\n{reason}\n"
          f"the queue is stopping; nothing else will be traced until this is "
          f"fixed\n{banner}", file=sys.stderr, flush=True)


def finish_infrastructure_failure(db, run_id, seed_id, number, review, usage=None, artifact=None):
    reason = feedback_from_review(review)
    finish_attempt(db, run_id, seed_id, number, "infrastructure_error", usage, review, reason, artifact)
    set_job(db, run_id, seed_id, "infrastructure_blocked", number, reason)
    alert_infrastructure_failure(seed_id, reason)
    # Marking one seed blocked and carrying on hid a broken environment for a
    # day while every seed it touched was quietly skipped.
    raise InfrastructureFailure(f"{seed_id}: {reason}")


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
    # Explicit queue entries win, then a plan's declared tracing priority, then
    # the order seeds loaded in. A seed authored from a prioritised plan reaches
    # the front without anyone remembering to enqueue it by hand.
    from seed_inventory import plan_trace_priorities
    trace_priority = plan_trace_priorities()
    seeds.sort(key=lambda seed: (seed["id"] not in queue_order,
                                queue_order.get(seed["id"], 0),
                                -trace_priority.get(seed["id"], 0)))
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
    exit_code = 0
    stop_dispatch = False
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
        while pending or futures or (supervised and not stop_dispatch):
            active_ids = {seed_id for seed_id in futures.values()}
            if supervised and not stop_dispatch:
                pending_ids = {seed["id"] for seed in pending}
                for seed in _selected(args):
                    if seed["id"] not in active_ids and seed["id"] not in pending_ids:
                        pending.append(seed)
                        pending_ids.add(seed["id"])
            target = configured_workers()
            while pending and not stop_dispatch and len(futures) < target:
                seed = pending.pop(0)
                future = pool.submit(run_one, seed)
                futures[future] = seed["id"]
            if not futures:
                if stop_dispatch:
                    break
                time.sleep(2)
                continue
            done, _ = wait(set(futures), timeout=2, return_when=FIRST_COMPLETED)
            for future in done:
                futures.pop(future, None)
                seed_id, code = future.result()
                completed += 1
                if code:
                    failures += 1
                    mapped = code if code in {USAGE_LIMIT_EXIT,
                                              INFRASTRUCTURE_EXIT} \
                        else INFRASTRUCTURE_EXIT
                    if exit_code == 0 or mapped == USAGE_LIMIT_EXIT:
                        exit_code = mapped
                    stop_dispatch = True
                    pending.clear()
                    print(f"[trace process failed: exit {mapped}] {seed_id}",
                          flush=True)
                else:
                    print(f"[trace complete: accepted] {seed_id}", flush=True)
    print(f"trace queue pass complete: {completed - failures} accepted, "
          f"{failures} failed processes, {completed} individual trace jobs", flush=True)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    original_argv = list(argv or [])
    defaults = CONFIG.get("pipeline", {}).get("trace", {})
    stepdown_enabled = bool(defaults.get("step_down_reasoning_on_failure", True))
    skip_judging = bool(defaults.get("skip_judging", False))
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
            coordinator_lock.close()
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
        skip_judging = bool(prior_limits.get("skip_judging", skip_judging))
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
    if skip_judging:
        # Loud, because every trace this run publishes will carry an
        # acceptance that no judge stands behind.
        print("  judge:  BYPASSED (pipeline.trace.skip_judging); every "
              "completed trace is accepted unjudged")
    else:
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

    ensure_publish_queue()
    limits = {"seeds": len(seeds), "max_attempts": args.max_attempts}
    limits["step_down_reasoning_on_failure"] = stepdown_enabled
    limits["skip_judging"] = skip_judging
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
        selected_teacher, capability_resolution = resolve_trace_harness(
            seed, configured_teacher=worker_teacher)
        if not skip_judging:
            worker_judge.preflight(require_auth=True)
        number = claim["attempts"] + 1
        configured_effort = str(selected_teacher.role.get("reasoning") or "max")
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
        effort = native_effort(selected_teacher.name, stage)
        attempt_teacher = (runtime_for_stage(selected_teacher, stage)
                           if stepdown_enabled else selected_teacher)
        has_more = next_reasoning_stage(required_stages,
                                        [*completed_stages, stage]) is not None
        from common import preflight_seed_environment, synthetic_tool_contract
        synthetic = synthetic_tool_contract(seed)
        if synthetic:
            set_job(worker_db, run_id, seed["id"], "infrastructure_blocked",
                    claim["attempts"], synthetic)
            alert_infrastructure_failure(seed["id"], synthetic)
            return
        environment_ok, environment_detail = preflight_seed_environment(
            seed, runtime=attempt_teacher)
        if not environment_ok:
            set_job(worker_db, run_id, seed["id"], "infrastructure_blocked",
                    claim["attempts"], environment_detail)
            alert_infrastructure_failure(seed["id"], environment_detail)
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
            record_model_call(worker_db, run_id)
            start_attempt(worker_db, run_id, seed["id"], number,
                          reasoning_stage=stage, reasoning_effort=effort)
            print(f"[{seed['id']}] attempt {number} ({stage}): author", flush=True)
            record = trace_task(seed, attempt_teacher, force=True,
                                reasoning_stage=stage,
                                capability_resolution=capability_resolution)
        usage = (record.get("teacher") or {}).get("usage") or {}
        # A deferral produced no candidate at all, so there is nothing for the
        # judge to read: `screen` would go looking for a raw trace that was
        # never written and raise, which lands back here as an infrastructure
        # failure and stops the queue — the outcome deferring exists to avoid.
        deferred = any(key.startswith("deferred_") and value
                       for key, value in record.items())
        if deferred:
            reason = str(record.get("deferral_reason") or "deferred")
            status = "retry" if has_more else "exhausted"
            finish_attempt(worker_db, run_id, seed["id"], number, status,
                           usage, None, reason)
            remove_completed_workspace(record)
            # Always to the tail, whatever retry_order says: an immediate retry
            # walks the same prompt back into whatever stopped it.
            if status == "retry":
                set_job(worker_db, run_id, seed["id"], "deferred", number, reason)
                status = "deferred"
            print(f"[{status}] {seed['id']}: {reason}", flush=True)
            return
        if skip_judging:
            # Deliberately outside the judge-error loop below: there is no
            # judge to have erred, and routing an unjudged review through
            # `is_judge_error` would let a failed deterministic setup re-enter
            # a re-review that can never happen.
            print(f"[{seed['id']}] attempt {number} ({stage}): unjudged",
                  flush=True)
            with lease_heartbeat():
                review = unjudged_review(seed)
            artifact = _archive_attempt(run_id, seed["id"], number)
            finish_attempt(worker_db, run_id, seed["id"], number, "accepted",
                           usage, review, artifact_path=artifact)
            remove_completed_workspace(record)
            print(f"[accepted:unjudged] {seed['id']}", flush=True)
            return
        # Candidate checks are evidence for the trace judge, never a separate
        # rejection gate. Every completed candidate proceeds to judgment.
        record_model_call(worker_db, run_id)
        print(f"[{seed['id']}] attempt {number} ({stage}): judge", flush=True)
        with lease_heartbeat():
            review = screen(seed, worker_judge)
        # A judge that returns no usable verdict has said nothing about this
        # trace, and `screen` budgets that: it reports which re-review this is
        # and stops counting the fault at JUDGE_ERROR_LIMIT, after which a
        # still-malformed verdict is a rejection and a still-broken judge is
        # the infrastructure failure below. Acting on the first one instead
        # stopped the whole queue over a single garbled reply — and re-tracing
        # would have been worse, re-billing a teacher generation to fix
        # something the teacher did not do.
        while (is_judge_error(review)
               and int(review.get("judge_errors") or 0) < JUDGE_ERROR_LIMIT):
            print(f"[{seed['id']}] attempt {number} ({stage}): re-judge "
                  f"({review.get('reason')})", flush=True)
            record_model_call(worker_db, run_id)
            with lease_heartbeat():
                review = screen(seed, worker_judge)
        if is_judge_error(review):
            artifact = _archive_attempt(run_id, seed["id"], number)
            finish_infrastructure_failure(worker_db, run_id, seed["id"], number,
                                          review, usage, artifact)
            return
        if is_accepted(review):
            artifact = _archive_attempt(run_id, seed["id"], number)
            finish_attempt(worker_db, run_id, seed["id"], number, "accepted",
                           usage, review, artifact_path=artifact)
            remove_completed_workspace(record)
            print(f"[accepted] {seed['id']}", flush=True)
            return
        status = "retry" if has_more else "exhausted"
        artifact = _archive_attempt(run_id, seed["id"], number)
        reason = feedback_from_review(review)
        finish_attempt(worker_db, run_id, seed["id"], number, status, usage,
                       review, reason, artifact_path=artifact)
        remove_completed_workspace(record)
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
        except NoCompatibleTraceHarness as error:
            if claim is not None:
                reason = f"{type(error).__name__}: {error}"
                set_job(worker_db, run_id, claim["seed_id"], "pending",
                        claim["attempts"], reason)
                alert_infrastructure_failure(claim["seed_id"], reason)
            with error_lock:
                worker_errors.append(error)
            stop_claiming.set()
        except BaseException as error:
            if claim is not None:
                reason = f"{type(error).__name__}: {error}"
                abandon_claim(worker_db, run_id, claim["seed_id"], owner, reason)
                alert_infrastructure_failure(claim["seed_id"], reason)
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
    except (InfrastructureFailure, NoCompatibleTraceHarness,
            TraceHarnessInfrastructureFailure) as broken:
        stop_claiming.set()
        for thread in threads.values(): thread.join()
        set_run_status(db, run_id, "stopped", str(broken))
        print(f"stopping: infrastructure failure: {broken}", file=sys.stderr)
        return INFRASTRUCTURE_EXIT
    except ModelUnavailable as blocked:
        # Out of quota is a live condition, not a failure to retry against: stop
        # the run and let the next start find out whether quota has returned.
        stop_claiming.set()
        for thread in threads.values(): thread.join()
        set_run_status(db, run_id, "stopped", str(blocked))
        print(f"stopping: {blocked}", file=sys.stderr)
        return USAGE_LIMIT_EXIT
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
