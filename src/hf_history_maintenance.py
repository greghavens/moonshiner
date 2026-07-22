"""Explicit Hugging Face history maintenance; never part of publication."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from configuration import PROJECT_ROOT, load_config


class HistorySafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    commit_id: str
    title: str
    created_at: datetime | None
    signature: str
    lfs_oids: set[str]


@dataclass(frozen=True)
class DeletionPlan:
    files: list
    bytes: int


def select_snapshots(commits: Iterable, loader: Callable, *, keep: int) -> list[Snapshot]:
    if keep < 1:
        raise HistorySafetyError("--keep must be positive")
    selected: list[Snapshot] = []
    signatures: set[str] = set()
    for commit in commits:
        snapshot = loader(commit)
        if snapshot is None or snapshot.signature in signatures:
            continue
        selected.append(snapshot)
        signatures.add(snapshot.signature)
        if len(selected) == keep:
            return selected
    raise HistorySafetyError(
        f"requested {keep} snapshots but only {len(selected)} verified snapshots exist")


def deletion_plan(lfs_files: Iterable, retained: Iterable[Snapshot], *,
                  protected_oids: Iterable[str] = ()) -> DeletionPlan:
    protected = ({oid for snapshot in retained for oid in snapshot.lfs_oids}
                 | set(protected_oids))
    files = sorted((item for item in lfs_files if item.file_oid not in protected),
                   key=lambda item: (item.filename, item.file_oid))
    return DeletionPlan(files, sum(int(item.size) for item in files))


def extra_ref_oids(api, dataset: str, refs, token) -> tuple[set[str], list[str]]:
    references = list(refs.branches) + list(refs.tags) + list(refs.converts)
    references += list(refs.pull_requests or [])
    names = sorted({item.ref for item in references
                    if item.ref != "refs/heads/main"})
    oids: set[str] = set()
    for name in names:
        tree = api.list_repo_tree(dataset, recursive=True, revision=name,
                                  repo_type="dataset", token=token)
        oids.update(item.lfs.sha256 for item in tree
                    if getattr(item, "lfs", None) is not None)
    return oids, names


def validate_manifest(manifest: object, available_paths: set[str]) -> bool:
    if not isinstance(manifest, dict):
        return False
    active = manifest.get("active_shards")
    shards = manifest.get("shards")
    if (manifest.get("schema_version") != 1
            or manifest.get("format") != "moonshiner-parquet-shards-v1"
            or not isinstance(active, list) or not active
            or not isinstance(shards, list)):
        return False
    if any(not isinstance(name, str) or name not in available_paths
           for name in active):
        return False
    shard_paths = [item.get("path") for item in shards
                   if isinstance(item, dict)]
    return (len(shard_paths) == len(shards)
            and len(set(active)) == len(active)
            and set(shard_paths) == set(active))


def _tree_snapshot(api, dataset: str, commit, token) -> Snapshot | None:
    from huggingface_hub import RepoFile, hf_hub_download
    files = [item for item in api.list_repo_tree(
        dataset, recursive=True, revision=commit.commit_id,
        repo_type="dataset", token=token) if isinstance(item, RepoFile)]
    by_path = {item.path: item for item in files}
    if "README.md" not in by_path:
        return None
    lfs_oids = {item.lfs.sha256 for item in files if item.lfs is not None}
    manifest_file = by_path.get("dataset-manifest.json")
    if manifest_file is not None:
        path = hf_hub_download(dataset, "dataset-manifest.json",
                               repo_type="dataset", revision=commit.commit_id,
                               token=token)
        try:
            manifest = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not validate_manifest(manifest, set(by_path)):
            return None
        signature = "parquet:" + json.dumps(
            manifest, sort_keys=True, separators=(",", ":"))
    else:
        canonical = by_path.get("traces.jsonl")
        if canonical is None:
            return None
        identity = (canonical.lfs.sha256 if canonical.lfs is not None else
                    canonical.xet_hash or canonical.blob_id)
        signature = "jsonl:" + str(identity)
    return Snapshot(commit.commit_id, commit.title, commit.created_at,
                    signature, lfs_oids)


def _publisher_name() -> str:
    import hashlib
    key = hashlib.sha256(str(PROJECT_ROOT).encode()).hexdigest()[:12]
    return f"moonshiner-publish-{key}.service"


def _pause_publisher() -> bool:
    unit = _publisher_name()
    active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit])
    if active.returncode != 0:
        return False
    subprocess.run(["systemctl", "--user", "kill", "--kill-whom=main",
                    "--signal=SIGSTOP", unit], check=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["systemctl", "--user", "show", unit,
             "--property=TasksCurrent", "--value"],
            text=True, capture_output=True, check=True)
        try:
            if int(result.stdout.strip() or "0") <= 1:
                return True
        except ValueError:
            pass
        time.sleep(1)
    subprocess.run(["systemctl", "--user", "kill", "--kill-whom=main",
                    "--signal=SIGCONT", unit])
    raise HistorySafetyError("publisher did not become idle within 10 minutes")


def _resume_publisher(paused: bool) -> None:
    if paused:
        subprocess.run(["systemctl", "--user", "kill", "--kill-whom=main",
                        "--signal=SIGCONT", _publisher_name()], check=True)


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1000 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1000
    raise AssertionError("unreachable")


def run(dataset: str, *, keep: int, execute: bool) -> int:
    from huggingface_hub import HfApi
    from publish import token as load_token
    auth = load_token()
    api = HfApi(token=auth)
    protected_oids, protected_refs = extra_ref_oids(
        api, dataset, api.list_repo_refs(
            dataset, repo_type="dataset", include_pull_requests=True, token=auth),
        auth)
    commits = api.list_repo_commits(dataset, repo_type="dataset", token=auth)
    retained = select_snapshots(
        commits, lambda commit: _tree_snapshot(api, dataset, commit, auth),
        keep=keep)
    plan = deletion_plan(api.list_lfs_files(
        dataset, repo_type="dataset", token=auth), retained,
        protected_oids=protected_oids)
    print(f"HF history maintenance: {dataset}")
    print(f"Retain: {len(retained)} verified snapshots")
    for snapshot in retained:
        stamp = snapshot.created_at.isoformat() if snapshot.created_at else "unknown"
        print(f"  {snapshot.commit_id[:12]}  {stamp}  {snapshot.title}")
    print(f"Protect: {len(protected_oids)} LFS objects reachable from "
          f"{len(protected_refs)} additional refs")
    print(f"Delete: {len(plan.files)} unretained LFS objects "
          f"({_format_bytes(plan.bytes)})")
    if not execute:
        print("Dry run only. Re-run with --yes to permanently rewrite HF history.")
        return 0
    if not plan.files:
        print("Nothing to delete; history is already within the retention policy.")
        return 0
    paused = _pause_publisher()
    try:
        api.permanently_delete_lfs_files(
            dataset, plan.files, rewrite_history=True,
            repo_type="dataset", token=auth)
        after = api.list_repo_commits(dataset, repo_type="dataset", token=auth)
        verified = select_snapshots(
            after, lambda commit: _tree_snapshot(api, dataset, commit, auth),
            keep=keep)
        if [item.signature for item in verified] != [
                item.signature for item in retained]:
            raise HistorySafetyError(
                "post-rewrite snapshot verification did not match the retention plan")
    finally:
        _resume_publisher(paused)
    print("HF history rewrite complete and retained snapshots verified.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="moonshiner maintenance prune-hf-history")
    parser.add_argument("--dataset", default=(load_config().get("publish") or {}).get(
        "hf_dataset"))
    parser.add_argument("--keep", type=int, default=10)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if not args.dataset:
        parser.error("--dataset is required when publish.hf_dataset is not configured")
    try:
        return run(args.dataset, keep=args.keep, execute=args.yes)
    except HistorySafetyError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
