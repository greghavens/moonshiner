"""Unified teacher trace generation.

Drives whichever runtime ``config.teacher.runtime`` selects (Codex, Claude Code,
or Pi/GLM) over the seed corpus, materializing a fresh Git workspace per seed,
running the teacher, then verifying by rejection sampling: an attempt is kept
only if the seed's ``verify_cmd`` passes on the resulting workspace and the
protected test files are byte-for-byte intact. Every attempt records model
attestation and hash pins so downstream screening can fail closed on a stale or
unattested trace. Deferrals (usage limit, safeguard refusal, model fallback) are
written as meta without a passing trace so a batch never burns blindly.

Runnable standalone (``python3 src/generate_traces.py --all``) or imported by the
single-process runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from common import (TRACES, clear_runtime_caches, git_diff,
                    load_seeds, materialize, protected_hashes, quarantined_tasks,
                    run_verify, scrub_text, seed_fingerprint)
from runtimes import (NoCompatibleTraceHarness,
                      TraceHarnessInfrastructureFailure, get_teacher,
                      resolve_trace_harness)
from runtimes.availability import (INFRASTRUCTURE_EXIT, USAGE_LIMIT_EXIT,
                                   ModelUnavailable)

RAW = TRACES / "raw"
META = TRACES / "meta"
DIFFS = TRACES / "diffs"

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def with_action_boundary(prompt: str, seed: dict,
                         feedback: str | None = None) -> str:
    """Return exactly the authored seed prompt for the native harness trace."""
    return prompt


def _trace_turns(seed: dict) -> tuple[str, list[str] | None]:
    turns = (seed.get("interaction") or {}).get("turns")
    if not turns:
        return seed["prompt"], None
    authored = [str(turn.get("content", turn)) for turn in turns]
    return authored[0], authored[1:] or None


def trace_task(seed: dict, teacher=None, *, force: bool = False,
               attempts: int = 1, feedback: str | None = None,
               reasoning_stage: str | None = None,
               traces_root: Path | None = None,
               capability_resolution: dict | None = None) -> dict:
    """Generate (and verify) one trace for ``seed``; return its meta record."""
    traces_root = traces_root or TRACES
    raw_dir = traces_root / "raw"
    meta_dir = traces_root / "meta"
    diffs_dir = traces_root / "diffs"
    for directory in (raw_dir, meta_dir, diffs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / f"{seed['id']}.json"
    if meta_path.exists() and not force:
        existing = json.loads(meta_path.read_text())
        if existing.get("passed"):
            return existing

    teacher = teacher or get_teacher()
    if capability_resolution is None:
        teacher, capability_resolution = resolve_trace_harness(
            seed, configured_teacher=teacher)
    prompt, interaction = _trace_turns(seed)

    best: dict | None = None
    for attempt in range(1, max(1, attempts) + 1):
        workspace = materialize(seed)
        protected_before = protected_hashes(seed, workspace)
        try:
            result = teacher.run_trace(
                seed, workspace, out_dir=raw_dir, system_prompt="",
                prompt=prompt, interaction=interaction,
                security=False, tools=None)
        except ModelUnavailable:
            raise
        except (SystemExit, Exception) as error:
            raise TraceHarnessInfrastructureFailure(
                f"selected trace harness {teacher.name!r} raised "
                f"{type(error).__name__}: {error}") from error

        # Out of quota is not this seed's problem: deferring it would march
        # through the corpus marking every remaining seed deferred. Stop, and
        # let the next start discover whether quota has returned.
        if result.unavailable:
            raise ModelUnavailable(f"{teacher.name}: {result.unavailable}")
        # Two different conditions, kept apart in provenance: a provider that
        # refused to answer, and an agent that stopped to ask a question no one
        # is there to answer. Both defer this seed and leave the run alone.
        # A kind is terminal when another attempt cannot change the answer. A
        # provider refuses the same prompt every time and bills a full prompt
        # cache write for each refusal, so retrying buys nothing; an agent that
        # asked a question may well not ask it again.
        for blocked, kind, terminal, fallback in (
                (result.safeguard_refusal, "safeguard_refusal", True,
                 "teacher issued a safeguard refusal"),
                (result.blocked_on_question, "interactive_question", False,
                 "teacher asked a question a headless trace cannot answer")):
            if not blocked:
                continue
            record = _deferral(seed, prompt, teacher, kind, terminal,
                               result.error or fallback, capability_resolution)
            _write_meta(meta_path, record)
            record["_workspace_path"] = str(workspace)
            return record
        if (result.timed_out or result.return_code != 0
                or not result.stream_success or result.error):
            if result.error:
                detail = result.error
            elif result.timed_out:
                detail = "trace harness timed out"
            elif result.return_code != 0:
                detail = f"trace harness exited {result.return_code}"
            else:
                detail = "trace harness stream did not complete successfully"
            raise TraceHarnessInfrastructureFailure(
                f"selected trace harness {teacher.name!r} failed: {detail}")

        clear_runtime_caches(workspace)
        passed, verify_output = run_verify(seed, workspace)
        protected_after = protected_hashes(seed, workspace)
        protected_intact = protected_before == protected_after
        diff = git_diff(workspace)
        (diffs_dir / f"{seed['id']}.patch").write_text(diff)

        raw_text = result.raw_path.read_text(errors="replace") \
            if result.raw_path.exists() else ""
        from task_environment import environment_provenance
        task_environment_provenance = environment_provenance(seed)
        record = {
            "id": seed["id"],
            "lang": seed.get("lang"),
            "category": seed.get("category"),
            "passed": (bool(passed) and protected_intact
                       and result.return_code == 0 and not result.timed_out
                       and result.stream_success and not result.error),
            "verify_passed": passed,
            "protected_intact": protected_intact,
            "verify_output": scrub_text(verify_output)[:8000],
            "setup_ok": True,
            "setup_output": "",
            "attempt": attempt,
            "return_code": result.return_code,
            "timed_out": result.timed_out,
            "duration_s": round(result.duration_s, 2),
            "stream_success": result.stream_success,
            "trace_format": result.trace_format,
            "prompt": prompt,
            "prompt_sha256": _sha256(prompt),
            "seed_fingerprint": seed_fingerprint(seed),
            "protected_hashes": protected_before,
            "raw_sha256": _sha256(raw_text),
            "diff_sha256": _sha256(diff),
            "raw_path": str(result.raw_path.relative_to(traces_root.parent)),
            "diff_path": str((diffs_dir / f"{seed['id']}.patch").relative_to(
                traces_root.parent)),
            "feedback_used": bool(feedback),
            "teacher": {
                "runtime": teacher.name,
                "model": teacher.role["model"],
                "reasoning": teacher.role.get("reasoning"),
                "reasoning_stage": reasoning_stage,
                "observed_model": result.observed_model,
                "observed_models": result.observed_models,
                "model_attested": result.model_attested,
                "model_fallback": result.model_fallback,
                "safeguard_refusal": result.safeguard_refusal,
                "usage": result.usage,
                "error": result.error,
                "provenance": {
                    **result.provenance,
                    "capability_resolution": capability_resolution,
                    "task_environment": task_environment_provenance,
                },
            },
        }
        _write_meta(meta_path, record)
        # The workspace is process-local lifecycle state.  Return it to the
        # queue runner without persisting it in canonical trace metadata.
        record["_workspace_path"] = str(workspace)
        best = record
        if record["passed"]:
            break
    return best or {}


def _deferral(seed: dict, prompt: str, teacher, kind: str, terminal: bool,
              detail: str, capability_resolution: dict) -> dict:
    return {
        "id": seed["id"],
        "passed": None,
        "prompt": prompt,
        "prompt_sha256": _sha256(prompt),
        "seed_fingerprint": seed_fingerprint(seed),
        f"deferred_{kind}": True,
        "deferral_reason": detail,
        "deferral_terminal": terminal,
        "teacher": {"runtime": teacher.name, "model": teacher.role["model"],
                    "reasoning": teacher.role.get("reasoning"),
                    "provenance": {
                        "capability_resolution": capability_resolution}},
    }


def _write_meta(path: Path, record: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate teacher traces.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="Trace every seed.")
    group.add_argument("--only", help="Comma-separated seed ids.")
    group.add_argument("--task", help="Single seed id.")
    parser.add_argument("--preflight", action="store_true",
                        help="Check the configured teacher runtime and exit.")
    parser.add_argument("--smoke", action="store_true",
                        help="Trace one seed (--only/--task) as a smoke test.")
    parser.add_argument("--force", action="store_true",
                        help="Re-run even if a passing trace exists.")
    parser.add_argument("--attempts", type=int, default=1,
                        help="Rejection-sampling attempts per seed.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Trace at most N seeds (0 = no limit).")
    parser.add_argument("--include-holdout", action="store_true",
                        help="Include holdout tasks (default excludes them).")
    args = parser.parse_args(argv)

    teacher = get_teacher()
    if args.preflight:
        teacher.preflight(require_auth=True)
        print(f"teacher OK: runtime={teacher.name} model={teacher.role['model']} "
              f"reasoning={teacher.role.get('reasoning')}")
        return 0
    only = None
    if args.only:
        only = {value.strip() for value in args.only.split(",") if value.strip()}
    elif args.task:
        only = {args.task}
    seeds = load_seeds(only=only, include_holdout=args.include_holdout)
    from import_existing import imported_task_ids
    imported = imported_task_ids()
    excluded = quarantined_tasks()
    seeds = [seed for seed in seeds
             if seed["id"] not in excluded and (args.force or seed["id"] not in imported)]
    if args.limit:
        seeds = seeds[:args.limit]
    if args.smoke:
        seeds = seeds[:1]
    if not seeds:
        print("no seeds matched", file=sys.stderr)
        return 1

    passed = failed = deferred = 0
    for seed in seeds:
        try:
            record = trace_task(
                seed, teacher, force=args.force, attempts=args.attempts)
        except ModelUnavailable as error:
            print(f"stopping: {error}", file=sys.stderr)
            return USAGE_LIMIT_EXIT
        except (NoCompatibleTraceHarness,
                TraceHarnessInfrastructureFailure) as error:
            print(f"stopping: infrastructure failure: {error}", file=sys.stderr)
            return INFRASTRUCTURE_EXIT
        status = ("passed" if record.get("passed")
                  else "deferred" if record.get("passed") is None else "failed")
        passed += status == "passed"
        failed += status == "failed"
        deferred += status == "deferred"
        print(f"[{status:8}] {seed['id']}")
    print(f"\n{passed} passed, {failed} failed, {deferred} deferred "
          f"of {len(seeds)} seeds")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
