#!/usr/bin/env python3
"""Continuously publish each newly accepted trajectory in its own HF commit."""
from __future__ import annotations

import fcntl
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from common import (CONFIG, DATA, ROOT, RUNS, TRACES,
                    deterministic_review_accepted)
from configuration import load_config


def accepted_tasks() -> list[tuple[float, str]]:
    ready = []
    for path in (TRACES / "reviews").glob("*.json"):
        try:
            review = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if (review.get("accepted") is True
                and deterministic_review_accepted(review)
                and (review.get("judge") or {}).get("model_attested") is True
                and (TRACES / "meta" / path.name).is_file()):
            ready.append((path.stat().st_mtime, path.stem))
    return sorted(ready)


def published_tasks(path: Path, max_rows: int | None = None) -> set[str]:
    tasks = set()
    if not path.is_file():
        return tasks
    with path.open() as handle:
        for number, line in enumerate(handle):
            if max_rows is not None and number >= max_rows:
                break
            if not line.strip():
                continue
            task = json.loads(line).get("task")
            if isinstance(task, str):
                tasks.add(task)
    return tasks


def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def verify_remote(dataset: str, title: str) -> None:
    url = f"https://huggingface.co/api/datasets/{dataset}/commits/main"
    with urllib.request.urlopen(url) as response:
        commits = json.load(response)
    if not any(commit.get("title") == title for commit in commits[:20]):
        raise RuntimeError(f"remote commit verification failed: {title}")


def batch_size() -> int:
    value = int((load_config().get("publish") or {}).get("batch_size", 1))
    if not 1 <= value <= 1000:
        raise ValueError("publish.batch_size must be from 1 through 1000")
    return value


def tracing_has_unfinished_work() -> bool:
    """Ledger state, not service liveness, decides final partial-batch flushes."""
    from run_state import connect
    db = connect()
    try:
        row = db.execute(
            "SELECT COUNT(*) FROM jobs j JOIN runs r ON r.id=j.run_id "
            "WHERE r.kind='trace' AND r.status='running' "
            "AND j.status IN ('pending','retry','running')").fetchone()
        return bool(row and row[0])
    finally:
        db.close()


def save_acknowledged(path: Path, tasks: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps({"published_tasks": sorted(tasks)}, indent=2) + "\n")
    pending.replace(path)


def main() -> int:
    dataset = (CONFIG.get("publish") or {}).get("hf_dataset")
    if not dataset:
        print("publish queue disabled: publish.hf_dataset is not configured", flush=True)
        return 0
    RUNS.mkdir(parents=True, exist_ok=True)
    lock = (RUNS / "publish-queue.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("publish queue already running", flush=True)
        return 0
    output = DATA / "hf-publish" / "traces.jsonl"
    acknowledgements = DATA / "hf-sync" / "published-trajectories.json"
    if acknowledgements.is_file():
        known = set(json.loads(acknowledgements.read_text()).get("published_tasks") or [])
    else:
        # The local canonical was downloaded from HF before append-only operation;
        # record that baseline once. Thereafter only remote-verified commits advance it.
        from hf_sync import ensure_local_dataset
        baseline = ensure_local_dataset(target=output)
        known = published_tasks(output, int(baseline.get("bootstrap_rows") or 0))
        save_acknowledged(acknowledgements, known)
    print(f"publish queue active: {dataset}; {len(known)} existing tasks", flush=True)
    while True:
        pending = [(stamp, task) for stamp, task in accepted_tasks() if task not in known]
        if not pending:
            time.sleep(2)
            continue
        size = batch_size()
        if len(pending) < size and tracing_has_unfinished_work():
            time.sleep(2)
            continue
        tasks = [task for _, task in pending[:size]]
        label = tasks[0] if len(tasks) == 1 else f"{tasks[0]}…{tasks[-1]} ({len(tasks)})"
        print(f"[publish] {label}: format", flush=True)
        run("src/build_dataset.py", "--quiet")
        run("src/expand_next_steps.py")
        export_args = ["src/export_hf_next_steps.py"]
        for task in tasks:
            export_args.extend(["--task", task])
        run(*export_args)
        title = (f"Add trajectory {tasks[0]}" if len(tasks) == 1 else
                 f"Add {len(tasks)} trajectories: {tasks[0]} through {tasks[-1]}")
        print(f"[publish] {label}: upload", flush=True)
        run("moonshiner.py", "publish", "--yes", "--commit-message",
            title)
        verify_remote(dataset, title)
        known.update(tasks)
        save_acknowledged(acknowledgements, known)
        print(f"[published] {label}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
