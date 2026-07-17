#!/usr/bin/env python3
"""moonshiner — single-process orchestrator for the full distillation pipeline.

One process, end to end: import and audit the seed corpus, drive the configured
teacher runtime to produce traces, screen them with the configured judge, then
build, expand, export, and stage the training dataset. Every phase is idempotent
and fail-closed, so an AI agent can run the whole thing with one command or drive
any single phase by name and re-run it safely.

  python3 moonshiner.py run                  # full pipeline, end to end
  python3 moonshiner.py run --from generate  # resume from a phase
  python3 moonshiner.py run --to screen      # stop after a phase
  python3 moonshiner.py run --offline        # only phases that call no model
  python3 moonshiner.py run --with validate  # fold in an optional phase
  python3 moonshiner.py run --dry-run        # print the plan, run nothing
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
    Phase("audit", 2, "Audit seed integrity (fail-closed)", "audit_seeds"),
    Phase("validate", 2.5, "Prove seed solvability, no model calls",
          "validate_seeds", optional=True),
    Phase("generate", 3, "Drive teacher to produce traces", "generate_traces",
          metered=True, run_argv=("--all",)),
    Phase("screen", 4, "Screen traces (deterministic gates + judge)",
          "screen_traces", metered=True, run_argv=("--all", "--review")),
    Phase("retry", 4.5, "Retrace + rescreen standing rejections",
          "retry_rejected_traces", metered=True, optional=True),
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


def _phase_help() -> str:
    lines = ["phases (canonical order; * = optional, folded in via run --with):"]
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
    if command == "run":
        return _run(rest)
    if command == "phases":
        print(_phase_help())
        return 0
    if command == "preflight":
        return _preflight(rest)
    if command in BY_KEY:
        return _dispatch(BY_KEY[command], rest)

    print(f"[moonshiner] unknown command: {command}", file=sys.stderr)
    print(_phase_help(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
