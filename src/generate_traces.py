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

from common import (TRACES, SYSTEM_PROMPT, clear_runtime_caches, git_diff,
                    load_seeds, materialize, protected_hashes, quarantined_tasks,
                    run_setup, run_verify, scrub_text, seed_fingerprint)
from runtimes import get_teacher
from runtimes.availability import ModelUnavailable

RAW = TRACES / "raw"
META = TRACES / "meta"
DIFFS = TRACES / "diffs"

TRACE_ACTION_BOUNDARY = "=== MOONSHINER TASK BOUNDARY ==="

RESEARCH_REMINDER = (
    "TRACE EXECUTION INTEGRITY REMINDER: This task requires consulting official "
    "documentation. Use WebSearch and WebFetch to read the official source before "
    "the first source-code mutation, and keep every action inside the provided "
    "task workspace.")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def with_action_boundary(prompt: str, seed: dict,
                         feedback: str | None = None) -> str:
    """Compose the user turn: reminders, the boundary sentinel, then the task."""
    parts = []
    if (seed.get("research") or {}).get("required"):
        parts.append(RESEARCH_REMINDER)
    parts.append(TRACE_ACTION_BOUNDARY)
    parts.append(prompt.strip())
    if feedback:
        parts.append("\nPRIOR ATTEMPT FEEDBACK (address before finishing):\n"
                     + feedback.strip())
    return "\n\n".join(parts)


def _interaction_turns(seed: dict) -> list[str] | None:
    turns = (seed.get("interaction") or {}).get("turns")
    if not turns:
        return None
    # First turn is the initial prompt; the rest are replayed follow-ups.
    return [str(turn.get("content", turn)) for turn in turns[1:]] or None


def trace_task(seed: dict, teacher=None, *, force: bool = False,
               attempts: int = 1, feedback: str | None = None) -> dict:
    """Generate (and verify) one trace for ``seed``; return its meta record."""
    for directory in (RAW, META, DIFFS):
        directory.mkdir(parents=True, exist_ok=True)
    meta_path = META / f"{seed['id']}.json"
    if meta_path.exists() and not force:
        existing = json.loads(meta_path.read_text())
        if existing.get("passed"):
            return existing

    teacher = teacher or get_teacher()
    prompt = with_action_boundary(seed["prompt"], seed, feedback)
    interaction = _interaction_turns(seed)
    tools = (seed.get("tool_harness") or {}).get("tools")

    best: dict | None = None
    for attempt in range(1, max(1, attempts) + 1):
        workspace = materialize(seed)
        setup_ok, setup_output = run_setup(seed, workspace)
        protected_before = protected_hashes(seed, workspace)
        try:
            result = teacher.run_trace(
                seed, workspace, out_dir=RAW, system_prompt=SYSTEM_PROMPT,
                prompt=prompt, interaction=interaction,
                security=False, tools=tools)
        except ModelUnavailable as blocked:
            record = _deferral(seed, prompt, teacher, "unavailable", str(blocked))
            _write_meta(meta_path, record)
            return record

        if result.unavailable:
            record = _deferral(seed, prompt, teacher, "unavailable", result.unavailable)
            _write_meta(meta_path, record)
            return record
        if result.safeguard_refusal:
            record = _deferral(seed, prompt, teacher, "safeguard_refusal",
                               "teacher issued a safeguard refusal")
            _write_meta(meta_path, record)
            return record

        clear_runtime_caches(workspace)
        passed, verify_output = run_verify(seed, workspace)
        protected_after = protected_hashes(seed, workspace)
        protected_intact = protected_before == protected_after
        diff = git_diff(workspace)
        (DIFFS / f"{seed['id']}.patch").write_text(diff)

        raw_text = result.raw_path.read_text(errors="replace") \
            if result.raw_path.exists() else ""
        record = {
            "id": seed["id"],
            "lang": seed.get("lang"),
            "category": seed.get("category"),
            "passed": (bool(passed) and protected_intact and setup_ok
                       and result.return_code == 0 and not result.timed_out
                       and result.stream_success and not result.error),
            "verify_passed": passed,
            "protected_intact": protected_intact,
            "verify_output": scrub_text(verify_output)[:8000],
            "setup_ok": setup_ok,
            "setup_output": scrub_text(setup_output)[:2000],
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
            "raw_path": str(result.raw_path.relative_to(TRACES.parent)),
            "diff_path": f"traces/diffs/{seed['id']}.patch",
            "feedback_used": bool(feedback),
            "teacher": {
                "runtime": teacher.name,
                "model": teacher.role["model"],
                "reasoning": teacher.role.get("reasoning"),
                "observed_model": result.observed_model,
                "observed_models": result.observed_models,
                "model_attested": result.model_attested,
                "model_fallback": result.model_fallback,
                "safeguard_refusal": result.safeguard_refusal,
                "usage": result.usage,
                "error": result.error,
                "provenance": result.provenance,
            },
        }
        _write_meta(meta_path, record)
        best = record
        if record["passed"]:
            break
    return best or {}


def _deferral(seed: dict, prompt: str, teacher, kind: str, detail: str) -> dict:
    return {
        "id": seed["id"],
        "passed": None,
        "prompt": prompt,
        "prompt_sha256": _sha256(prompt),
        "seed_fingerprint": seed_fingerprint(seed),
        f"deferred_{kind}": True,
        "deferral_reason": detail,
        "teacher": {"runtime": teacher.name, "model": teacher.role["model"],
                    "reasoning": teacher.role.get("reasoning")},
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
    teacher.preflight(require_auth=True)

    only = None
    if args.only:
        only = {value.strip() for value in args.only.split(",") if value.strip()}
    elif args.task:
        only = {args.task}
    seeds = load_seeds(only=only, include_holdout=args.include_holdout)
    excluded = quarantined_tasks()
    seeds = [seed for seed in seeds if seed["id"] not in excluded]
    if args.limit:
        seeds = seeds[:args.limit]
    if args.smoke:
        seeds = seeds[:1]
    if not seeds:
        print("no seeds matched", file=sys.stderr)
        return 1

    passed = failed = deferred = 0
    for seed in seeds:
        record = trace_task(seed, teacher, force=args.force, attempts=args.attempts)
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
