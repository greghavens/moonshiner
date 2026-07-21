#!/usr/bin/env python3
"""Continuously publish each newly accepted trajectory in its own HF commit."""
from __future__ import annotations

import fcntl
import json
import subprocess
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from common import CONFIG, DATA, ROOT, RUNS, TRACES
from configuration import PROJECT_ROOT, load_config
from review_contract import is_accepted


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_suffix(target.suffix + ".accepted.pending")
    shutil.copy2(source, pending)
    pending.replace(target)


def restore_hidden_acceptances() -> list[str]:
    """Restore canonical files from durable accepted attempts.

    Older queue code could overwrite an accepted canonical trace with a later
    rejected attempt. Attempt archives are immutable, so they are the source of
    truth. Current files are already preserved in their own attempt archive.
    """
    from run_state import connect
    db = connect()
    try:
        rows = db.execute(
            "SELECT a.seed_id,a.artifact_path FROM attempts a "
            "JOIN runs r ON r.id=a.run_id "
            "WHERE r.kind='trace' AND a.status='accepted' "
            "AND a.artifact_path IS NOT NULL ORDER BY a.id").fetchall()
    finally:
        db.close()
    latest = {str(row[0]): Path(str(row[1])) for row in rows}
    restored = []
    for seed_id, archive in latest.items():
        canonical_review = TRACES / "reviews" / f"{seed_id}.json"
        try:
            current = json.loads(canonical_review.read_text())
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            current = None
        if is_accepted(current):
            continue
        review_path = archive / "reviews.json"
        meta_path = archive / "meta.json"
        try:
            review = json.loads(review_path.read_text())
            meta = json.loads(meta_path.read_text())
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
        if (not is_accepted(review) or review.get("id") != seed_id
                or meta.get("id") != seed_id):
            continue
        raw_name = Path(str(meta.get("raw_path") or f"{seed_id}.jsonl")).name
        raw_path = archive / raw_name
        if not raw_path.is_file():
            continue
        _atomic_copy(meta_path, TRACES / "meta" / f"{seed_id}.json")
        _atomic_copy(review_path, canonical_review)
        _atomic_copy(raw_path, TRACES / "raw" / raw_name)
        archived_diff = archive / "diffs.patch"
        if archived_diff.is_file():
            _atomic_copy(archived_diff, TRACES / "diffs" / f"{seed_id}.patch")
        restored.append(seed_id)
    return restored


def accepted_tasks(accepted: set[str] | None = None, *,
                   validate_artifacts: bool = True) -> list[tuple[float, str, int]]:
    from run_state import connect
    db = connect()
    try:
        if accepted is None:
            from seed_inventory import accepted_ids
            accepted = accepted_ids(db)
        versions = {str(row[0]): int(row[1]) for row in db.execute(
            "SELECT a.seed_id,MAX(a.id) FROM attempts a "
            "JOIN runs r ON r.id=a.run_id "
            "WHERE r.kind='trace' AND a.status='accepted' GROUP BY a.seed_id")}
    finally:
        db.close()
    if not validate_artifacts:
        return sorted((0.0, task, versions[task]) for task in accepted
                      if task in versions)
    ready = []
    for path in (TRACES / "reviews").glob("*.json"):
        try:
            review = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if (path.stem in accepted and is_accepted(review)
                and (TRACES / "meta" / path.name).is_file()):
            ready.append((path.stat().st_mtime, path.stem,
                          versions.get(path.stem, 0)))
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


def built_tasks() -> set[str]:
    """Return trajectories that the formatter actually produced."""
    tasks = set()
    for split in ("train", "val"):
        path = DATA / "next_step" / f"{split}.jsonl"
        if not path.is_file():
            continue
        with path.open() as handle:
            for line in handle:
                if not line.strip():
                    continue
                task = (json.loads(line).get("meta") or {}).get("task")
                if isinstance(task, str):
                    tasks.add(task)
    return tasks


def run(*args: str) -> None:
    script, *rest = args
    subprocess.run([sys.executable, str(ROOT / script), *rest],
                   cwd=PROJECT_ROOT, check=True)


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


def save_acknowledged(path: Path, attempts: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps({"published_tasks": sorted(attempts),
        "published_attempts": dict(sorted(attempts.items()))}, indent=2) + "\n")
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
    restored = restore_hidden_acceptances()
    if restored:
        print(f"restored {len(restored)} hidden accepted trajectories", flush=True)
    output = DATA / "hf-publish" / "traces.jsonl"
    acknowledgements = DATA / "hf-sync" / "published-trajectories.json"
    if acknowledgements.is_file():
        state = json.loads(acknowledgements.read_text())
        known = set(state.get("published_tasks") or [])
        published_attempts = {str(k): int(v) for k, v in
                              (state.get("published_attempts") or {}).items()}
        if not published_attempts:
            current = {task: version for _, task, version in accepted_tasks()}
            published_attempts = {task: current.get(task, 0) for task in known}
            save_acknowledged(acknowledgements, published_attempts)
    else:
        # The local canonical was downloaded from HF before append-only operation;
        # record that baseline once. Thereafter only remote-verified commits advance it.
        from hf_sync import ensure_local_dataset
        baseline = ensure_local_dataset(target=output)
        known = published_tasks(output, int(baseline.get("bootstrap_rows") or 0))
        current = {task: version for _, task, version in accepted_tasks()}
        published_attempts = {task: current.get(task, 0) for task in known}
        save_acknowledged(acknowledgements, published_attempts)
    print(f"publish queue active: {dataset}; {len(known)} existing tasks", flush=True)
    blocked: set[str] = set()
    while True:
        pending = [(stamp, task, version)
                   for stamp, task, version in accepted_tasks()
                   if (task not in known
                       or version > published_attempts.get(task, -1))
                   and task not in blocked]
        if not pending:
            time.sleep(2)
            continue
        size = batch_size()
        if len(pending) < size and tracing_has_unfinished_work():
            time.sleep(2)
            continue
        batch = pending[:size]
        tasks = [task for _, task, _ in batch]
        label = tasks[0] if len(tasks) == 1 else f"{tasks[0]}…{tasks[-1]} ({len(tasks)})"
        print(f"[publish] {label}: format", flush=True)
        run("src/build_dataset.py", "--quiet")
        run("src/expand_next_steps.py")
        available = built_tasks()
        missing = [task for task in tasks if task not in available]
        if missing:
            blocked.update(missing)
            for task in missing:
                print(f"[publish blocked] {task}: accepted artifact is not buildable",
                      flush=True)
            tasks = [task for task in tasks if task in available]
        if not tasks:
            continue
        label = tasks[0] if len(tasks) == 1 else f"{tasks[0]}…{tasks[-1]} ({len(tasks)})"
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
        for _, task, version in batch:
            if task in tasks:
                published_attempts[task] = version
        save_acknowledged(acknowledgements, published_attempts)
        print(f"[published] {label}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
