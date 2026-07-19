"""Bounded Codex authoring pass over the non-code behavior-seed corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from common import BEHAVIOR_SEEDS_DIR, BEHAVIOR_WORLDS, CONFIG, ROOT, RUNS, TRACES
from runtimes import get_seed_author

SEEDS = BEHAVIOR_SEEDS_DIR
WORLDS = BEHAVIOR_WORLDS
SCHEMA = ROOT / "schemas" / "behavior_seed.schema.json"
STATE = RUNS / "behavior-seed-authoring.json"

SYSTEM = """You are authoring non-code tool-behavior training seeds for Moonshiner.
Work only on the behavior-*.json files already present in the workspace. Do not
create, rename, or delete seeds. Do not edit the schema, world registry, or
instructions. Preserve each seed's id, category, world, and primary behavioral
objective. Improve the seed itself: realistic wording, internally consistent
arguments and fixture state, precise expected stages, useful distractors,
instruction-following constraints, and non-code subject matter. Calls within a
parallel stage must be independent and must remain in one assistant action;
dependent calls belong in later stages. Never copy BFCL questions or answers.
Every expected tool must exist in the selected world. Return a brief summary
after editing every assigned file. All actions are fictional simulator state;
never connect to a live service, use real credentials, or cause external side
effects."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_state() -> dict:
    if not STATE.exists():
        return {"schema_version": 1, "completed": {}}
    return json.loads(STATE.read_text())


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def status() -> dict:
    state = load_state()
    all_paths = sorted(SEEDS.glob("behavior-*.json"))
    current = {path.stem: digest(path) for path in all_paths}
    completed = {seed_id: value for seed_id, value in state.get("completed", {}).items()
                 if current.get(seed_id) == value}
    return {"total": len(all_paths), "sol_authored": len(completed),
            "remaining": len(all_paths) - len(completed),
            "model": state.get("last_model")}


def audit() -> None:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "audit_behavior_seeds.py")],
                          cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError("behavior seed audit failed:\n" + proc.stdout + proc.stderr)


def main(argv=None) -> int:
    original_argv = list(argv or [])
    parser = argparse.ArgumentParser(prog="moonshiner behavior-seed author")
    select = parser.add_mutually_exclusive_group()
    select.add_argument("--all", action="store_true")
    select.add_argument("--only", help="Comma-separated behavior seed IDs")
    parser.add_argument("--limit", type=int, help="Maximum unfinished seeds")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--yes", action="store_true", help="Authorize metered Codex calls")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--detach", action="store_true",
                        help="Run the resumable authoring batches in a durable user scope")
    args = parser.parse_args(argv)
    if args.batch_size < 1 or args.batch_size > 120:
        parser.error("--batch-size must be from 1 through 120")
    if not args.all and not args.only:
        parser.error("select --all or --only; coding seeds are never selected")

    author = get_seed_author()
    if author.name != "codex" or author.role.get("model") != "gpt-5.6-sol":
        parser.error("behavior seeds require seed-author codex/gpt-5.6-sol; configure that role first")
    wanted = set(args.only.split(",")) if args.only else None
    state = load_state()
    paths = [p for p in sorted(SEEDS.glob("behavior-*.json"))
             if wanted is None or p.stem in wanted]
    paths = [p for p in paths if state["completed"].get(p.stem) != digest(p)]
    if args.limit is not None:
        paths = paths[:args.limit]
    batches = [paths[i:i + args.batch_size] for i in range(0, len(paths), args.batch_size)]
    print(f"behavior-seed author plan: {len(paths)} unfinished seed(s), "
          f"{len(batches)} Codex batch call(s), batch size <= {args.batch_size}")
    if not paths:
        return 0
    if args.dry_run:
        for batch in batches:
            print("  " + ",".join(p.stem for p in batch))
        return 0
    if args.detach:
        command = [str(ROOT / "scripts" / "batch.sh"), "behavior-seeds",
                   sys.executable, str(ROOT / "moonshiner.py"), "behavior-seed",
                   "author", *[value for value in original_argv if value != "--detach"]]
        return subprocess.run(command).returncode
    if not args.yes:
        print("refusing metered authoring without --yes", file=sys.stderr)
        return 2
    author.preflight(require_auth=True)
    (TRACES / "behavior-authoring").mkdir(parents=True, exist_ok=True)

    workspace_root = RUNS / "behavior-seed-workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)
    for index, batch in enumerate(batches, 1):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            workspace = workspace_root / f"{stamp}-{uuid.uuid4().hex[:8]}"
            # A batch always starts in a new path. We never clear, reuse, or
            # remove an authoring workspace, regardless of promotion outcome.
            workspace.mkdir()
            for source in batch:
                shutil.copy2(source, workspace / source.name)
            shutil.copy2(WORLDS, workspace / WORLDS.name)
            shutil.copy2(SCHEMA, workspace / SCHEMA.name)
            (workspace / "INSTRUCTIONS.md").write_text(SYSTEM + "\n")
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            subprocess.run(["git", "-c", "user.email=harness@moonshiner",
                            "-c", "user.name=moonshiner harness", "add", "-A"],
                           cwd=workspace, check=True)
            subprocess.run(["git", "-c", "user.email=harness@moonshiner",
                            "-c", "user.name=moonshiner harness", "commit", "-qm",
                            "behavior seed authoring batch"], cwd=workspace, check=True)
            before = {p.name: digest(workspace / p.name) for p in batch}
            batch_record = {"status": "authoring", "workspace": str(workspace),
                            "seed_ids": [p.stem for p in batch],
                            "model": author.role["model"]}
            (workspace / "BATCH.json").write_text(
                json.dumps(batch_record, indent=2, sort_keys=True) + "\n")
            prompt = (f"Author and critically improve all {len(batch)} assigned behavior seeds. "
                      "Read INSTRUCTIONS.md, behavior-worlds.json, and behavior_seed.schema.json first. "
                      "Inspect every behavior-*.json file, edit every file where any improvement is possible, "
                      "and ensure the batch collectively remains diverse. Do not merely review them.")
            print(f"[{index}/{len(batches)}] Codex authoring {batch[0].stem}..{batch[-1].stem}", flush=True)
            artifact_id = f"behavior-author-{batch[0].stem}-{batch[-1].stem}"
            result = author.run_trace({"id": artifact_id}, workspace,
                                      out_dir=TRACES / "behavior-authoring",
                                      system_prompt=SYSTEM, prompt=prompt,
                                      security=True)
            if result.unavailable or result.timed_out or result.return_code not in (0, None):
                raise RuntimeError(result.unavailable or result.error or f"Codex batch {index} failed")
            present = sorted(p.name for p in workspace.glob("behavior-*.json")
                             if p.name != WORLDS.name)
            assigned = sorted(p.name for p in batch)
            if present != assigned:
                raise RuntimeError(f"Codex changed batch membership: expected {assigned}, got {present}")
            for source in batch:
                candidate = workspace / source.name
                value = json.loads(candidate.read_text())
                if value.get("id") != source.stem or value.get("kind") != "tool_behavior":
                    raise RuntimeError(f"Codex changed identity/kind in {source.name}")
                shutil.copy2(candidate, source)
            audit()
            from corpus import write_catalog
            write_catalog(SEEDS.parent / "seeds")
            changed = sum(before[p.name] != digest(p) for p in batch)
            for source in batch:
                state["completed"][source.stem] = digest(source)
            state["last_model"] = author.role["model"]
            state["completed_count"] = len(state["completed"])
            batch_record.update(status="promoted", edited=changed)
            (workspace / "BATCH.json").write_text(
                json.dumps(batch_record, indent=2, sort_keys=True) + "\n")
            save_state(state)
            print(f"[{index}/{len(batches)}] accepted {len(batch)} seeds; Codex edited {changed}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
