"""Seed author → deterministic validation → judge repair → promotion."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from common import CONFIG, ROOT, SEEDS_DIR, TRACES, WORKSPACES
from run_state import (connect, create_run, finish_attempt, set_run_status,
                       start_attempt)
from runtimes import get_seed_author, get_seed_judge
from validate_seeds import validate_report

SCHEMA = json.loads((ROOT / "schemas" / "author_review_verdict.schema.json").read_text())
CANDIDATES = ROOT / "tasks" / "candidates"

AUTHOR_SYSTEM = """You author deterministic coding repair seeds for Moonshiner. Work only in the current workspace. Create exactly task.json, files/, and reference_fix.patch at the workspace root. The starting files must contain one focused defect; protected tests must expose it; the reference patch must fix it without modifying tests. Commands must be offline and deterministic. Do not run another coding agent."""


def _init_workspace(seed_id: str) -> Path:
    workspace = WORKSPACES / f"author-{seed_id}"
    if workspace.exists(): shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    return workspace


def _load_candidate(directory: Path, expected_id: str) -> dict:
    task = directory / "task.json"
    if not task.exists(): raise ValueError("author did not create task.json")
    seed = json.loads(task.read_text())
    if seed.get("id") != expected_id:
        raise ValueError(f"task id must be {expected_id!r}")
    seed["_dir"] = directory
    return seed


def _review_prompt(seed: dict, report: dict) -> str:
    return f"""Review and, when possible, FIX this authored Moonshiner seed in place.
You may edit task.json, files/, tests, and reference_fix.patch. Preserve the core objective; repair prompt/test mismatches, weak tests, unrelated baseline bugs, broken patches, and nondeterminism. After edits, return only the required JSON verdict. Use verdict=accept only if the resulting on-disk seed is ready. Use needs_human if fixing it would redefine the objective.

SEED ID: {seed['id']}
DETERMINISTIC VALIDATION BEFORE YOUR REVIEW:
{json.dumps(report, indent=2)}
"""


def main(argv: list[str] | None = None) -> int:
    defaults = CONFIG.get("pipeline", {}).get("seed", {})
    parser = argparse.ArgumentParser(
        description="Author, validate, judge/fix, and promote one new seed.")
    parser.add_argument("--id", required=True, help="New unique seed id.")
    parser.add_argument("--brief", required=True, help="Seed objective and constraints.")
    parser.add_argument("--max-attempts", type=int,
                        default=int(defaults.get("max_attempts", 2)))
    parser.add_argument("--yes", action="store_true",
                        help="Authorize metered author and judge calls.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.id):
        parser.error("--id must use lowercase letters, digits, and hyphens")
    destination = SEEDS_DIR / args.id
    if destination.exists():
        print(f"refusing to replace existing seed: {destination}", file=sys.stderr); return 2
    author, judge = get_seed_author(), get_seed_judge()
    print(f"seed plan: author {author.name}/{author.role['model']} → validate → "
          f"judge/fix {judge.name}/{judge.role['model']} → promote {args.id}")
    if args.dry_run: return 0
    if not args.yes:
        print("refusing metered seed authoring without --yes", file=sys.stderr); return 2
    author.preflight(require_auth=True); judge.preflight(require_auth=True)
    db = connect(); run_id = create_run(db, "seed", {
        "author": {"runtime": author.name, **author.role},
        "judge": {"runtime": judge.name, **judge.role}},
        {"max_attempts": args.max_attempts}, [args.id])
    workspace = _init_workspace(args.id)
    candidate = CANDIDATES / run_id / args.id
    try:
        # The author call creates the initial candidate. Later calls belong to the
        # judge, which is explicitly allowed to repair the candidate in place.
        dummy = {"id": f"seed-author-{args.id}"}
        (TRACES / "raw").mkdir(parents=True, exist_ok=True)
        (TRACES / "reviews").mkdir(parents=True, exist_ok=True)
        authored = author.run_trace(dummy, workspace, out_dir=TRACES / "raw",
                                    system_prompt=AUTHOR_SYSTEM, prompt=args.brief)
        if (authored.unavailable or authored.timed_out or authored.safeguard_refusal
                or authored.return_code not in (0, None)):
            raise RuntimeError(authored.unavailable or authored.error
                               or "seed author failed to complete")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(workspace, candidate, ignore=shutil.ignore_patterns(".git"))
        seed = _load_candidate(candidate, args.id)
        accepted = False
        for number in range(1, args.max_attempts + 1):
            start_attempt(db, run_id, args.id, number)
            report = validate_report(seed)
            review = judge.run_review(_review_prompt(seed, report), candidate,
                                      out_dir=TRACES / "reviews", schema=SCHEMA,
                                      read_only=False)
            # Reload judge edits and prove the final on-disk form independently.
            seed = _load_candidate(candidate, args.id)
            final_report = validate_report(seed)
            verdict = review.verdict or {}
            categories = ("scope_creep", "missed_requirements", "extra_requirements",
                          "seed_code_bugs", "reference_fix_bugs", "weak_tests",
                          "nondeterminism")
            verdict_clear = (verdict.get("verdict") == "accept"
                             and all(not (verdict.get(key) or {}).get("found")
                                     for key in categories)
                             and any(item.get("seed_id") == args.id
                                     and item.get("status") == "accept"
                                     for item in verdict.get("seed_reviews", [])))
            accepted = final_report.get("passed") is True and verdict_clear
            status = "accepted" if accepted else ("retry" if number < args.max_attempts else "exhausted")
            error = None if accepted else "; ".join(final_report.get("failures") or [verdict.get("summary", "judge rejected")])
            finish_attempt(db, run_id, args.id, number, status,
                           review=verdict, error=error)
            print(f"[{status}] {args.id}{': ' + error if error else ''}")
            if accepted: break
        if not accepted:
            set_run_status(db, run_id, "complete_with_rejections")
            print(f"candidate retained at {candidate}")
            return 1
        shutil.copytree(candidate, destination)
        set_run_status(db, run_id, "complete")
        print(f"promoted seed: {destination}")
        return 0
    except Exception as error:
        set_run_status(db, run_id, "failed", f"{type(error).__name__}: {error}")
        raise
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
