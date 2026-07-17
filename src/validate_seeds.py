#!/usr/bin/env python3
"""Full local solvability validation for seeds without a passing trace.

Completeness (``audit_seeds``) is not proof of validity: an authoring agent can
die between finishing a seed and running its own self-check. This proves each
complete seed is actually solvable, with no model calls, in a fresh workspace —
  1. verify FAILS at baseline (the task really starts broken/unbuilt)
  2. ``reference_fix.patch`` applies cleanly (git apply)
  3. protected test files are byte-identical after the patch
  4. ``reference_setup`` succeeds and leaves protected tests unchanged
  5. verify PASSES twice after the patch (the task is genuinely solvable)
  6. reversing the patch makes the baseline fail again
  7. after clearing runtime caches the workspace restores clean
The workspace is deleted afterwards; trace generation re-materializes its own.

Seeds with a runtime-debug contract also get their non-mutating ``build_cmd``
exercised before and after the fix. No model calls anywhere — free to re-run.
  python3 src/validate_seeds.py
  python3 src/validate_seeds.py --only go-worker-pool,ts-pagination
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from common import (TRACES, clear_runtime_caches, load_seeds, materialize,
                    run_setup, run_verify, test_file_hashes)


def run_runtime_build(seed: dict, workspace: Path) -> str | None:
    """Exercise a runtime-debug seed's non-mutating build; None if clean/absent."""
    runtime = seed.get("runtime_debug") or {}
    if runtime.get("required") is not True:
        return None
    try:
        result = subprocess.run(
            shlex.split(runtime["build_cmd"]), cwd=workspace,
            capture_output=True, text=True,
            timeout=min(int(seed.get("verify_timeout", 300)), 300),
        )
    except (KeyError, ValueError, subprocess.TimeoutExpired) as error:
        return f"runtime build command failed to run: {error}"
    if result.returncode != 0:
        tail = (result.stdout + "\n" + result.stderr).strip()[-300:]
        return f"runtime build fails before reproduction: {tail}"
    return None


def trace_passed(seed_id: str) -> bool:
    meta = TRACES / "meta" / f"{seed_id}.json"
    if not meta.exists():
        return False
    try:
        return bool(json.loads(meta.read_text()).get("passed"))
    except json.JSONDecodeError:
        return False


def _verification(seed: dict, workspace: Path) -> dict:
    passed, output = run_verify(seed, workspace)
    return {"passed": passed, "tail": (output or "")[-2000:]}


def validate_report(seed: dict) -> dict:
    """Return fail-closed, reviewer-readable evidence for one seed."""
    workspace = materialize(seed, name=f"validate-{seed['id']}")
    report = {
        "id": seed["id"],
        "passed": False,
        "failures": [],
        "baseline_runs": [],
        "reference_runs": [],
        "restored_baseline_runs": [],
        "reference_setup": None,
        "runtime_caches_removed": [],
        "restored_workspace_clean": False,
    }

    def fail(message: str) -> dict:
        report["failures"].append(message)
        return report

    try:
        patch = seed["_dir"] / "reference_fix.patch"
        initial_tests = test_file_hashes(seed, workspace)
        runtime_build_error = run_runtime_build(seed, workspace)
        if runtime_build_error:
            return fail(runtime_build_error)
        if test_file_hashes(seed, workspace) != initial_tests:
            return fail("runtime build modifies protected test files")
        report["baseline_runs"] = [_verification(seed, workspace) for _ in range(2)]
        if not all(run["passed"] is False for run in report["baseline_runs"]):
            observed = [run["passed"] for run in report["baseline_runs"]]
            return fail(f"verify did not fail twice at baseline (got {observed})")

        before = test_file_hashes(seed, workspace)
        applied = subprocess.run(["git", "apply", str(patch)], cwd=workspace,
                                 capture_output=True, text=True)
        if applied.returncode != 0:
            return fail(f"patch failed to apply: {applied.stderr.strip()[:200]}")
        if test_file_hashes(seed, workspace) != before:
            return fail("patch modifies protected test files")

        setup_ok, setup_output = run_setup(seed, workspace)
        report["reference_setup"] = {"passed": setup_ok,
                                     "tail": (setup_output or "")[-2000:]}
        if not setup_ok:
            return fail(f"reference_setup failed: {(setup_output or '')[-300:]}")
        if test_file_hashes(seed, workspace) != before:
            return fail("reference_setup modifies protected test files")
        runtime_build_error = run_runtime_build(seed, workspace)
        if runtime_build_error:
            return fail(f"after reference fix: {runtime_build_error}")
        if test_file_hashes(seed, workspace) != before:
            return fail("runtime build after patch modifies protected test files")
        report["reference_runs"] = [_verification(seed, workspace) for _ in range(2)]
        if not all(run["passed"] is True for run in report["reference_runs"]):
            tails = [run["tail"][-200:] for run in report["reference_runs"]]
            return fail(f"verify does not pass twice after patch: {tails}")
        if test_file_hashes(seed, workspace) != before:
            return fail("verification modifies protected test files")

        report["runtime_caches_removed"] = clear_runtime_caches(workspace)
        reverse = subprocess.run(["git", "apply", "-R", str(patch)], cwd=workspace,
                                 capture_output=True, text=True)
        if reverse.returncode != 0:
            return fail(f"patch failed to reverse: {reverse.stderr.strip()[:200]}")
        if test_file_hashes(seed, workspace) != initial_tests:
            return fail("reversing patch does not restore protected tests")
        runtime_build_error = run_runtime_build(seed, workspace)
        if runtime_build_error:
            return fail(f"after reversing reference fix: {runtime_build_error}")
        report["restored_baseline_runs"] = [
            _verification(seed, workspace) for _ in range(2)]
        if not all(run["passed"] is False
                   for run in report["restored_baseline_runs"]):
            observed = [run["passed"] for run in report["restored_baseline_runs"]]
            return fail(f"reversed baseline did not fail twice (got {observed})")

        clear_runtime_caches(workspace)
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=workspace, capture_output=True, text=True, check=True).stdout.strip()
        report["restored_workspace_clean"] = not status
        if status:
            return fail(f"workspace is not clean after patch reversal: {status[:500]}")
        report["passed"] = True
        return report
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def validate(seed: dict) -> str | None:
    report = validate_report(seed)
    return None if report["passed"] else "; ".join(report["failures"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="Comma-separated seed ids to validate")
    args = parser.parse_args(argv)

    only = {value.strip() for value in args.only.split(",")} if args.only else None
    valid, invalid, skipped = [], [], []
    for seed in load_seeds(only=only):
        seed_id = seed["id"]
        if trace_passed(seed_id):
            skipped.append(seed_id)  # already proven by a passing teacher trace
            continue
        if not (seed["_dir"] / "reference_fix.patch").exists():
            skipped.append(seed_id)  # holdout/pilot: trace- or eval-proven
            continue
        why = validate(seed)
        if why:
            invalid.append((seed_id, why))
            print(f"[INVALID] {seed_id}: {why}")
        else:
            valid.append(seed_id)
            print(f"[valid  ] {seed_id}")
    print(f"\n{len(valid)} valid, {len(invalid)} invalid, "
          f"{len(skipped)} skipped (trace-proven or exempt)")
    if invalid:
        print("invalid-dirs:", " ".join(seed_id for seed_id, _ in invalid))
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
