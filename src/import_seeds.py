#!/usr/bin/env python3
"""Import the seed corpus from the configured source repositories.

The moonshiner seed corpus is tracked in-tree under ``tasks/seeds``. Rather than
author seeds here, we take the latest vetted corpus from a canonical source and,
where the canonical seed is missing or broken, from a fallback source.

  * ``config.source.seed_repository`` — an optional canonical external source.
    Without one, the bundled corpus is canonical.
  * ``config.source.fallback_repository`` — an optional fallback source. Used
    only for a seed the canonical source lacks or left
    incomplete (an authoring agent that died mid-write, a safeguard-rejected
    stub). This encodes the rule "canonical unless it is off, then fall back".

A seed is complete when its ``task.json`` parses and carries every required
field, its ``id`` matches the directory name, ``files/`` exists, and every
protected ``test_files`` entry is present. A seed that is incomplete in BOTH
sources is reported invalid and never half-copied. Seeds already present here
are left untouched unless ``--force``.

Copies are atomic (stage into a sibling temp dir, then swap) and skip installed
``node_modules``/cache trees so a dependency install never bloats the corpus.

Model-free and idempotent — safe to re-run.
  python3 src/import_seeds.py            # canonical + fallback per config
  python3 src/import_seeds.py --dry-run  # report provenance without copying
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from common import CONFIG, ROOT, SEEDS_DIR, STORAGE_ROOT

APPROVED_MANIFEST_ID = "nvidia-open-swe-qwen35-nonthinking-10000-v1"
APPROVED_SELECTED_DIGEST = \
    "cc6025dfe583cffcc1e3909a2be611997c2b25db4036b143926bed2ad4fcecf4"
APPROVED_NVIDIA_REVISION = "ad4805a5aa7de70d99cab0bb8f99b15304c76de0"
APPROVED_TASK_REVISION = "475dd5e8703bb5fb22dd3c60b5d038b019eba1e0"
MANIFEST_SOURCE_COLUMNS = (
    "instance_id", "repo", "base_commit", "problem_statement", "image_name",
    "language", "license", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS",
    "install_config",
)

REQUIRED = ("id", "lang", "category", "prompt", "verify_cmd", "test_files")


def seeds_dir(repo: str) -> Path:
    """Resolve a source repo's ``tasks/seeds`` directory (relative to root)."""
    base = Path(repo).expanduser()
    if not base.is_absolute():
        base = (ROOT / base).resolve()
    return base / "tasks" / "seeds"


def source_seeds_dir(source: str | None = None) -> Path:
    """The canonical source ``tasks/seeds`` directory (config or override)."""
    repo = source or CONFIG.get("source", {}).get("seed_repository")
    return seeds_dir(repo) if repo else ROOT / "tasks" / "seeds"


def seed_complete(directory: Path) -> str | None:
    """Return a reason string if the source seed is NOT a complete unit."""
    if not directory.is_dir():
        return "directory absent"
    task_path = directory / "task.json"
    if not task_path.exists():
        return "no task.json"
    try:
        task = json.loads(task_path.read_text())
    except json.JSONDecodeError as error:
        return f"task.json invalid: {error}"
    missing = [key for key in REQUIRED if not task.get(key)]
    if missing:
        return f"task.json missing {missing}"
    if task["id"] != directory.name:
        return f"id {task['id']!r} != dir name"
    files = directory / "files"
    if not files.is_dir():
        return "no files/"
    absent = [name for name in task["test_files"] if not (files / name).exists()]
    if absent:
        return f"test files absent: {absent}"
    return None


def copy_seed(source_dir: Path, dest_dir: Path) -> None:
    """Copy one seed atomically: stage into a sibling temp dir, then swap in."""
    staging = dest_dir.with_name(dest_dir.name + ".importing")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(
        source_dir, staging,
        ignore=shutil.ignore_patterns("node_modules", "__pycache__", "*.pyc"))
    shutil.rmtree(dest_dir, ignore_errors=True)
    staging.replace(dest_dir)


def resolve(name: str, primary: Path, fallback: Path | None) -> tuple[Path, str, str]:
    """Choose the source directory for one seed id.

    Prefer the canonical source when complete; otherwise fall back. Returns
    ``(chosen_dir, provenance, reason)`` where provenance is ``primary`` /
    ``fallback`` / ``invalid`` and reason explains an invalid outcome.
    """
    primary_why = seed_complete(primary / name)
    if primary_why is None:
        return primary / name, "primary", ""
    if fallback is not None:
        fallback_why = seed_complete(fallback / name)
        if fallback_why is None:
            return fallback / name, "fallback", ""
        return primary / name, "invalid", (
            f"canonical: {primary_why}; fallback: {fallback_why}")
    return primary / name, "invalid", f"canonical: {primary_why}"


def load_selection_manifest(path: Path) -> dict:
    """Load and prove the exact approved 10,000-task selection manifest."""
    manifest = json.loads(Path(path).read_text())
    if manifest.get("manifest_id") != APPROVED_MANIFEST_ID:
        raise ValueError(f"unexpected manifest_id: {manifest.get('manifest_id')!r}")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 10_000:
        raise ValueError("approved manifest must contain exactly 10,000 tasks")
    ids = [task.get("instance_id") for task in tasks]
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("every manifest task must have a nonempty instance_id")
    if len(set(ids)) != 10_000:
        raise ValueError("approved manifest instance_id values must be unique")
    if ids != sorted(ids):
        raise ValueError("approved manifest instance_id values must be sorted")
    digest = hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()
    if digest != APPROVED_SELECTED_DIGEST \
            or manifest.get("selected_instance_ids_sha256") != digest:
        raise ValueError(f"approved manifest selected-ID digest mismatch: {digest}")
    sources = manifest.get("sources") or {}
    trajectory = sources.get("trajectory_dataset") or {}
    source = sources.get("task_dataset") or {}
    if trajectory.get("revision") != APPROVED_NVIDIA_REVISION:
        raise ValueError("approved NVIDIA source revision mismatch")
    if (source.get("dataset"), source.get("revision"), source.get("split")) != (
            "nebius/SWE-rebench-V2", APPROVED_TASK_REVISION, "train"):
        raise ValueError("approved SWE-rebench-V2 source identity mismatch")
    return manifest


def download_manifest_source(manifest: dict) -> Path:
    """Download the pinned task Parquet into this project's persistent state."""
    from huggingface_hub import hf_hub_download, list_repo_files
    source = manifest["sources"]["task_dataset"]
    cache = STORAGE_ROOT / "imports" / manifest["manifest_id"] / "hf-cache"
    cache.mkdir(parents=True, exist_ok=True)
    files = list_repo_files(
        source["dataset"], repo_type="dataset", revision=source["revision"])
    parquet = sorted(name for name in files
                     if name.startswith("data/") and name.endswith(".parquet"))
    if len(parquet) != 1:
        raise ValueError(
            f"pinned task source must contain exactly one data Parquet, got {parquet}")
    return Path(hf_hub_download(
        repo_id=source["dataset"], repo_type="dataset",
        revision=source["revision"], filename=parquet[0], cache_dir=cache))


def resolve_source_rows(instance_ids: list[str], parquet_paths: list[Path]
                        ) -> dict[str, dict]:
    """Resolve every selected ID exactly once using only approved source fields."""
    import pyarrow.parquet as parquet
    selected = set(instance_ids)
    found: dict[str, dict] = {}
    duplicate: set[str] = set()
    for path in parquet_paths:
        source = parquet.ParquetFile(path)
        available = set(source.schema_arrow.names)
        missing = set(MANIFEST_SOURCE_COLUMNS) - available
        if missing:
            raise ValueError(f"pinned source is missing columns: {sorted(missing)}")
        for batch in source.iter_batches(columns=list(MANIFEST_SOURCE_COLUMNS)):
            for row in batch.to_pylist():
                instance_id = row["instance_id"]
                if instance_id not in selected:
                    continue
                if instance_id in found:
                    duplicate.add(instance_id)
                else:
                    found[instance_id] = row
    missing_ids = [value for value in instance_ids if value not in found]
    if duplicate or missing_ids:
        detail = []
        if missing_ids:
            detail.append(f"missing {len(missing_ids)}: {missing_ids[:5]}")
        if duplicate:
            detail.append(f"duplicated {len(duplicate)}: {sorted(duplicate)[:5]}")
        raise ValueError("selected IDs must resolve exactly once; " + "; ".join(detail))
    return {instance_id: found[instance_id] for instance_id in instance_ids}


def canonical_manifest_seed(manifest: dict, selection: dict,
                            row: dict) -> tuple[dict, str]:
    """Map one selected source row into Moonshiner's canonical seed structure."""
    instance_id = selection["instance_id"]
    for field in ("instance_id", "repo", "license"):
        expected = instance_id if field == "instance_id" else selection[field]
        if row[field] != expected:
            raise ValueError(
                f"manifest/source mismatch for {instance_id} {field}: "
                f"{expected!r} != {row[field]!r}")
    source_language = row["language"]
    manifest_language = selection["language"]
    declared_language = {"ts": "typescript", "js": "javascript"}.get(
        source_language, source_language)
    if declared_language != manifest_language:
        raise ValueError(
            f"manifest/source mismatch for {instance_id} language: "
            f"{manifest_language!r} != {source_language!r}")
    prompt = row["problem_statement"]
    test_patch = row["test_patch"]
    install_config = row["install_config"]
    if not isinstance(prompt, str) or not isinstance(test_patch, str):
        raise ValueError(f"{instance_id}: source prompt and test_patch must be strings")
    if not isinstance(install_config, dict):
        raise ValueError(f"{instance_id}: install_config must be an object")
    test_cmd = install_config.get("test_cmd")
    if not isinstance(test_cmd, str) or not test_cmd.strip():
        raise ValueError(f"{instance_id}: install_config.test_cmd is missing")
    source = manifest["sources"]["task_dataset"]
    task = {
        "id": instance_id,
        "lang": row["language"],
        "category": selection["category"],
        "program": "NVIDIA Open-SWE 10K",
        "prompt": prompt,
        "verify_cmd": test_cmd,
        "environment": {
            "type": "oci",
            "image": row["image_name"],
            "repository": row["repo"],
            "base_commit": row["base_commit"],
            "workspace": "/testbed",
            "test_patch": "files/.moonshiner/test.patch",
            "fail_to_pass": list(row["FAIL_TO_PASS"] or []),
            "pass_to_pass": list(row["PASS_TO_PASS"] or []),
            "install_config": install_config,
        },
        "provenance": {
            "manifest_id": manifest["manifest_id"],
            "selected_instance_ids_sha256":
                manifest["selected_instance_ids_sha256"],
            "selection_dataset_revision":
                manifest["sources"]["trajectory_dataset"]["revision"],
            "task_dataset": source["dataset"],
            "task_dataset_revision": source["revision"],
            "task_dataset_split": source["split"],
            "instance_id": instance_id,
        },
    }
    return task, test_patch


def _write_manifest_seed(destination: Path, task: dict, test_patch: str) -> None:
    staging = destination.with_name(destination.name + ".importing")
    shutil.rmtree(staging, ignore_errors=True)
    patch = staging / "files" / ".moonshiner" / "test.patch"
    patch.parent.mkdir(parents=True)
    patch.write_text(test_patch)
    (staging / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n")
    staging.replace(destination)


def import_manifest(path: Path, *, destination: Path = SEEDS_DIR,
                    dry_run: bool = False,
                    source_parquet: Path | None = None) -> dict:
    """Import exactly the manifest selection through existing immutable semantics."""
    manifest = load_selection_manifest(path)
    parquet = source_parquet or download_manifest_source(manifest)
    selections = manifest["tasks"]
    instance_ids = [task["instance_id"] for task in selections]
    rows = resolve_source_rows(instance_ids, [Path(parquet)])
    prepared = [canonical_manifest_seed(
        manifest, selection, rows[selection["instance_id"]])
                for selection in selections]
    imported: list[str] = []
    skipped: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)
    for selection, (task, test_patch) in zip(selections, prepared, strict=True):
        seed_id = selection["instance_id"]
        target = destination / seed_id
        if target.exists():
            skipped.append(seed_id)
            continue
        if not dry_run:
            _write_manifest_seed(target, task, test_patch)
        imported.append(seed_id)
    if not dry_run and imported:
        from corpus import write_catalog
        write_catalog(destination)
    result = {
        "manifest_id": manifest["manifest_id"],
        "selected_instance_ids_sha256":
            manifest["selected_instance_ids_sha256"],
        "selected": len(instance_ids), "imported": len(imported),
        "skipped": len(skipped), "dry_run": dry_run,
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source",
                        help="Override config.source.seed_repository (canonical)")
    parser.add_argument("--fallback",
                        help="Override config.source.fallback_repository")
    parser.add_argument("--only", help="Comma-separated seed ids to import")
    parser.add_argument("--manifest", type=Path,
                        help="Import the approved pinned task-selection manifest")
    parser.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be imported without copying")
    args = parser.parse_args(argv)
    if args.force:
        parser.error("--force was removed: existing seeds are immutable")
    if args.manifest is not None:
        if args.source or args.fallback or args.only:
            parser.error("--manifest cannot be combined with --source, --fallback, or --only")
        try:
            result = import_manifest(args.manifest, dry_run=args.dry_run)
        except (OSError, ValueError) as error:
            print(f"manifest import failed: {error}", file=sys.stderr)
            return 1
        verb = "would import" if args.dry_run else "imported"
        print(f"{result['manifest_id']}: {result['imported']} {verb}, "
              f"{result['skipped']} skipped, {result['selected']} selected; "
              f"digest {result['selected_instance_ids_sha256']}")
        return 0

    primary = source_seeds_dir(args.source)
    if not primary.is_dir():
        print(f"canonical seed directory not found: {primary}", file=sys.stderr)
        return 1
    fallback_repo = args.fallback or CONFIG.get("source", {}).get("fallback_repository")
    fallback = seeds_dir(fallback_repo) if fallback_repo else None
    if fallback is not None and not fallback.is_dir():
        print(f"fallback seed directory not found: {fallback}", file=sys.stderr)
        return 1

    only = {value.strip() for value in args.only.split(",")} if args.only else None
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    candidates = {p.name for p in primary.iterdir() if p.is_dir()}
    if fallback is not None:
        candidates |= {p.name for p in fallback.iterdir() if p.is_dir()}

    imported, backfilled, skipped, invalid = [], [], [], []
    for name in sorted(candidates):
        if only and name not in only:
            continue
        chosen, provenance, reason = resolve(name, primary, fallback)
        if provenance == "invalid":
            invalid.append((name, reason))
            continue
        dest = SEEDS_DIR / name
        if dest.exists():
            skipped.append(name)
            continue
        if not args.dry_run:
            copy_seed(chosen, dest)
        imported.append(name)
        if provenance == "fallback":
            backfilled.append(name)

    for name, why in invalid:
        print(f"[invalid ] {name}: {why}")
    if backfilled:
        print(f"[fallback] {len(backfilled)} from fallback: {', '.join(backfilled)}")
    total = len(imported) + len(skipped) + len(invalid)
    verb = "would import" if args.dry_run else "imported"
    print(f"\n{len(imported)} {verb} ({len(backfilled)} via fallback), "
          f"{len(skipped)} skipped (already present), {len(invalid)} invalid "
          f"of {total} candidate seeds\n  canonical: {primary}\n  fallback:  "
          f"{fallback if fallback is not None else '(none)'}")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
