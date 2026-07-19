#!/usr/bin/env python3
"""moonshiner — bounded seed and trace quality pipelines.

The normal `run` command safely generates, verifies, judges, and when necessary
replaces traces. Seed authoring and dataset building are separate explicit
workflows. The original low-level phase runner remains available as `pipeline`.

  python3 moonshiner.py run                  # safe trace run (one seed)
  python3 moonshiner.py run --limit 20 --yes # bounded trace quality run
  python3 moonshiner.py run --detach         # durable background run
  python3 moonshiner.py pipeline --dry-run   # advanced legacy phase plan
  python3 moonshiner.py dataset build        # build/export accepted traces
  python3 moonshiner.py phases               # list the pipeline
  python3 moonshiner.py preflight            # check teacher + judge reachable
  python3 moonshiner.py generate --all       # one phase, its own native args
  python3 moonshiner.py screen --help        # per-phase help passes through

The teacher and judge runtimes (and their models/reasoning) are read from
config.json — see `runtimes` / `teacher` / `judge`. Metered phases preflight
their runtime and fail closed if it is unreachable, unauthenticated, or (for a
paid runtime) not credit-unlocked; nothing is silently skipped.
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@dataclass(frozen=True)
class Phase:
    """One pipeline step, dispatched to a src module's ``main``."""

    key: str            # short CLI name (run --from/--to/--skip and subcommand)
    order: float        # position in the canonical pipeline
    title: str          # one-line human description
    module: str         # module under src/ whose main() runs this phase
    metered: bool = False        # True if it drives the teacher/judge (spends)
    takes_argv: bool = True      # False if the module's main() takes no argv
    run_argv: tuple[str, ...] = ()   # args used when invoked as part of `run`
    optional: bool = False       # excluded from the default full run


# The canonical pipeline. `run` executes the non-optional phases in `order`;
# optional phases (solvability proof, repair lane) fold in only via --with.
PHASES: tuple[Phase, ...] = (
    Phase("import", 1, "Import seed corpus (canonical + fallback)",
          "import_seeds"),
    Phase("sec-import", 1.1, "Import fable-secure security cases (catalog + keys)",
          "import_security_cases", optional=True),
    Phase("sec-fetch", 1.2, "Hydrate the pinned security-review repositories",
          "fetch_security_corpus", optional=True),
    Phase("audit", 2, "Audit seed integrity (fail-closed)", "audit_seeds"),
    Phase("validate", 2.5, "Prove seed solvability, no model calls",
          "validate_seeds", optional=True),
    Phase("generate", 3, "Drive teacher to produce traces", "generate_traces",
          metered=True, run_argv=("--all",)),
    Phase("sec-generate", 3.1, "Drive teacher to produce security traces",
          "generate_security_traces", metered=True, optional=True,
          run_argv=("--all",)),
    Phase("screen", 4, "Screen traces (deterministic gates + judge)",
          "screen_traces", metered=True, run_argv=("--all", "--review")),
    Phase("retry", 4.5, "Retrace + rescreen standing rejections",
          "retry_rejected_traces", metered=True, optional=True),
    Phase("sec-build", 4.9, "Build the security SFT partition (folds into build)",
          "build_security_dataset", optional=True),
    Phase("build", 5, "Build the SFT dataset from accepted traces",
          "build_dataset", takes_argv=False),
    Phase("expand", 6, "Expand cumulative next-step prefixes",
          "expand_next_steps", takes_argv=False),
    Phase("export", 7, "Export the HF dataset", "export_hf", takes_argv=False),
    Phase("export-next", 8, "Export the HF next-steps dataset",
          "export_hf_next_steps", takes_argv=False),
    Phase("parquet", 9, "Export Parquet shards", "export_parquet",
          takes_argv=False),
    Phase("prepare", 10, "Stage the dataset for local training",
          "prepare_local", takes_argv=False),
    Phase("verify-export", 11, "Validate the exported dataset",
          "validate_hf_export", takes_argv=False),
    Phase("card", 11.5, "Render the Hugging Face dataset card",
          "export_hf_card", takes_argv=False),
)

BY_KEY = {phase.key: phase for phase in PHASES}
FULL = tuple(p for p in PHASES if not p.optional)


def _dispatch(phase: Phase, argv: list[str]) -> int:
    """Import the phase's module and call its main, returning an exit code."""
    module = importlib.import_module(phase.module)
    if phase.takes_argv:
        return int(module.main(argv) or 0)
    if argv:
        print(f"[moonshiner] {phase.key}: ignoring args {argv} "
              f"(phase takes none)", file=sys.stderr)
    return int(module.main() or 0)


def _plan(start: str | None, stop: str | None, include: list[str],
          skip: list[str], offline: bool) -> list[Phase]:
    """Resolve the ordered list of phases a `run` should execute."""
    chosen = list(FULL)
    for key in include:
        phase = BY_KEY[key]
        if phase not in chosen:
            chosen.append(phase)
    chosen.sort(key=lambda phase: phase.order)
    if start is not None:
        lo = BY_KEY[start].order
        chosen = [p for p in chosen if p.order >= lo]
    if stop is not None:
        hi = BY_KEY[stop].order
        chosen = [p for p in chosen if p.order <= hi]
    if offline:
        chosen = [p for p in chosen if not p.metered]
    return [p for p in chosen if p.key not in set(skip)]


def _run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="moonshiner.py run",
        description="Run the pipeline end to end (idempotent, fail-closed).")
    parser.add_argument("--from", dest="start", choices=[p.key for p in FULL],
                        help="Start at this phase (inclusive).")
    parser.add_argument("--to", dest="stop", choices=[p.key for p in FULL],
                        help="Stop after this phase (inclusive).")
    parser.add_argument("--with", dest="include", action="append", default=[],
                        choices=[p.key for p in PHASES if p.optional],
                        help="Fold in an optional phase at its natural place.")
    parser.add_argument("--skip", action="append", default=[],
                        choices=[p.key for p in PHASES],
                        help="Skip a phase.")
    parser.add_argument("--offline", action="store_true",
                        help="Run only phases that call no model.")
    parser.add_argument("--continue-on-error", action="store_true",
                        help="Keep going after a failing phase (default: stop).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan without running anything.")
    args = parser.parse_args(argv)

    plan = _plan(args.start, args.stop, args.include, args.skip, args.offline)
    if not plan:
        print("[moonshiner] empty plan — nothing to run", file=sys.stderr)
        return 1

    print(f"[moonshiner] plan ({len(plan)} phases):")
    for index, phase in enumerate(plan, 1):
        meter = "metered" if phase.metered else "offline"
        argv_note = f"  {' '.join(phase.run_argv)}" if phase.run_argv else ""
        print(f"  {index:>2}. {phase.key:<14} [{meter}] {phase.title}{argv_note}")
    if args.dry_run:
        return 0

    failures = []
    for index, phase in enumerate(plan, 1):
        header = f"[moonshiner] ({index}/{len(plan)}) {phase.key}"
        print(f"\n{header} — {phase.title}", flush=True)
        started = time.monotonic()
        try:
            code = _dispatch(phase, list(phase.run_argv))
        except SystemExit as exit_error:      # a phase may raise SystemExit(msg)
            code = exit_error.code if isinstance(exit_error.code, int) else 1
            if exit_error.code and not isinstance(exit_error.code, int):
                print(exit_error.code, file=sys.stderr)
        except KeyboardInterrupt:
            print(f"{header}: interrupted", file=sys.stderr)
            return 130
        except Exception as error:            # noqa: BLE001 - report and stop
            print(f"{header} RAISED {type(error).__name__}: {error}",
                  file=sys.stderr)
            code = 1
        elapsed = time.monotonic() - started
        if code == 0:
            print(f"{header}: ok ({elapsed:.1f}s)", flush=True)
            continue
        print(f"{header}: FAILED rc={code} ({elapsed:.1f}s)", file=sys.stderr)
        failures.append(phase.key)
        if not args.continue_on_error:
            print(f"[moonshiner] stopping at {phase.key} (fail-closed)",
                  file=sys.stderr)
            return code
    if failures:
        print(f"\n[moonshiner] completed with failures: {', '.join(failures)}",
              file=sys.stderr)
        return 1
    print(f"\n[moonshiner] pipeline complete: {len(plan)} phases ok")
    return 0


def _preflight(argv: list[str]) -> int:
    """Report whether the configured teacher and judge are usable right now."""
    parser = argparse.ArgumentParser(prog="moonshiner.py preflight")
    parser.add_argument("--require-auth", action="store_true", default=True,
                        help="Require authentication (default).")
    parser.parse_args(argv)

    from runtimes import get_judge, get_teacher       # lazy: offline cmds skip
    from runtimes.availability import (ModelUnavailable,  # noqa: F401
                                       active_block, require_available)

    ok = True
    seen: dict[str, str] = {}
    for role, runtime in (("teacher", get_teacher()), ("judge", get_judge())):
        label = f"{role}: {runtime.name}"
        block = active_block(runtime.name)
        if block:
            print(f"[{label}] UNAVAILABLE until {block.get('retry_at', '?')}: "
                  f"{block.get('reason', '')}", file=sys.stderr)
            ok = False
            continue
        # Two roles can share one runtime — preflight it once.
        if runtime.name in seen:
            print(f"[{label}] {seen[runtime.name]} (shared with earlier role)")
            continue
        try:
            runtime.preflight(require_auth=True)
        except Exception as error:            # noqa: BLE001 - report per role
            print(f"[{label}] preflight FAILED: {type(error).__name__}: {error}",
                  file=sys.stderr)
            seen[runtime.name] = "failed"
            ok = False
            continue
        seen[runtime.name] = "ready"
        print(f"[{label}] ready")
    return 0 if ok else 1


def _config(argv: list[str]) -> int:
    from configuration import (LOCAL_PATH, dotted_get, load_config, parse_value,
                               update_local)
    parser = argparse.ArgumentParser(prog="moonshiner.py config")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("show")
    get = sub.add_parser("get"); get.add_argument("key")
    setp = sub.add_parser("set"); setp.add_argument("key"); setp.add_argument("value")
    role = sub.add_parser("role")
    role.add_argument("role", choices=["trace-author", "trace-judge", "seed-author", "seed-judge"])
    role.add_argument("runtime"); role.add_argument("model"); role.add_argument("reasoning", nargs="?")
    args = parser.parse_args(argv)
    if args.action == "show":
        print(json.dumps(load_config(), indent=2)); return 0
    if args.action == "get":
        try: value = dotted_get(load_config(), args.key)
        except KeyError: print(f"unknown config key: {args.key}", file=sys.stderr); return 2
        print(json.dumps(value) if not isinstance(value, str) else value); return 0
    if args.action == "role":
        prefix = {"trace-author": "teacher", "trace-judge": "judge",
                  "seed-author": "seed_author", "seed-judge": "seed_judge"}[args.role]
        path = update_local(prefix + ".runtime", args.runtime)
        update_local(prefix + ".model", args.model)
        if args.reasoning: update_local(prefix + ".reasoning", args.reasoning)
        print(f"configured {args.role}: {args.runtime}/{args.model} in {path.relative_to(ROOT)}")
        return 0
    path = update_local(args.key, parse_value(args.value))
    print(f"set {args.key} in {path.relative_to(ROOT)}")
    return 0


def _status(argv: list[str], *, inspect: bool = False) -> int:
    from run_state import connect, job_rows, summaries
    parser = argparse.ArgumentParser(prog=f"moonshiner.py {'inspect' if inspect else 'status'}")
    parser.add_argument("run_id", nargs="?", help="Run id (latest when omitted for inspect).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    db = connect(); rows = summaries(db, args.run_id)
    if inspect and not args.run_id and rows: args.run_id = rows[0]["id"]; rows = rows[:1]
    if not rows:
        print("no matching runs"); return 1
    payload = rows
    if inspect:
        payload = [{**rows[0], "jobs_detail": job_rows(db, args.run_id)}]
    if args.json: print(json.dumps(payload, indent=2)); return 0
    for row in payload:
        print(f"{row['id']}  {row['status']}  accepted={row.get('accepted') or 0}/"
              f"{row.get('jobs') or 0} failed={row.get('failed') or 0} "
              f"pending={row.get('pending') or 0}")
        if inspect:
            for job in row["jobs_detail"]:
                detail = f": {job['last_error']}" if job.get("last_error") else ""
                print(f"  [{job['status']:<10}] {job['seed_id']} "
                      f"attempts={job['attempts']}{detail}")
    return 0


def _phase_help() -> str:
    lines = ["advanced pipeline phases (* = optional via pipeline --with):"]
    for phase in sorted(PHASES, key=lambda p: p.order):
        mark = "*" if phase.optional else " "
        meter = "metered" if phase.metered else "offline"
        lines.append(f" {mark} {phase.key:<14} [{meter}] {phase.title}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        print("\n" + _phase_help())
        return 0

    command, rest = argv[0], argv[1:]
    if command in {"pipeline", "legacy-run"}:
        return _run(rest)
    if command == "phases":
        print(_phase_help())
        return 0
    if command == "preflight":
        return _preflight(rest)
    if command == "config":
        return _config(rest)
    if command in {"run", "trace", "trace-run"}:
        if command == "trace" and rest and rest[0] == "run": rest = rest[1:]
        from trace_pipeline import main as trace_main
        return trace_main(rest)
    if command == "status":
        return _status(rest)
    if command == "inspect":
        return _status(rest, inspect=True)
    if command == "auth":
        from control_cli import auth_main
        return auth_main(rest)
    if command == "doctor":
        from control_cli import doctor_main
        return doctor_main(rest)
    if command in {"seed", "seed-run"}:
        if command == "seed" and rest and rest[0] == "run": rest = rest[1:]
        from seed_pipeline import main as seed_main
        return seed_main(rest)
    if command == "dataset":
        if not rest or rest[0] not in {"build", "export"}:
            print("usage: moonshiner.py dataset {build,export}", file=sys.stderr)
            return 2
        return _run(["--from", "build"])
    if command in BY_KEY:
        return _dispatch(BY_KEY[command], rest)

    print(f"[moonshiner] unknown command: {command}", file=sys.stderr)
    print(_phase_help(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
