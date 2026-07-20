#!/usr/bin/env python3
"""Moonshiner creates and reviews coding-agent training data."""
from __future__ import annotations

import argparse
import getpass
import importlib
import os
import shutil
import subprocess
import sys
import time
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "0.3.0"
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
    Phase("verify-export", 9, "Validate the exported dataset",
          "validate_hf_export", takes_argv=False),
    Phase("parquet", 10, "Export validated Parquet shards", "export_parquet",
          takes_argv=False),
    Phase("card", 11, "Render the Hugging Face dataset card",
          "export_hf_card", takes_argv=False),
    Phase("prepare", 12, "Legacy local tokenizer rendering (explicit opt-in)",
          "prepare_local", takes_argv=False, optional=True),
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
        prog="moonshiner pipeline",
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
    parser = argparse.ArgumentParser(prog="moonshiner preflight")
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
    parser = argparse.ArgumentParser(prog="moonshiner config")
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
        print(f"configured {args.role}: {args.runtime}/{args.model} in {path}")
        return 0
    value = parse_value(args.value)
    bounded = {"pipeline.trace.workers": (1, 64),
               "pipeline.seed.workers": (1, 64),
               "publish.batch_size": (1, 1000)}
    if args.key in bounded:
        low, high = bounded[args.key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            parser.error(f"{args.key} must be an integer from {low} through {high}")
    path = update_local(args.key, value)
    print(f"set {args.key} in {path}")
    return 0


def _ask(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def _yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{hint}]: ").strip().lower()
    if not value: return default
    return value in {"y", "yes"}


PROVIDER_PRESETS = {
    "openrouter": {"display_provider": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "api": "openai-completions", "thinking_format": "openrouter",
                   "key_env": "OPENROUTER_API_KEY"},
    "openai": {"display_provider": "OpenAI", "base_url": "https://api.openai.com/v1",
               "api": "openai-completions", "key_env": "OPENAI_API_KEY"},
    "anthropic": {"display_provider": "Anthropic", "base_url": "https://api.anthropic.com",
                  "api": "anthropic-messages", "thinking_format": "anthropic",
                  "key_env": "ANTHROPIC_API_KEY"},
}


def _configure_pi_provider(config: dict, current_runtime: str) -> tuple[str, dict]:
    existing = (config.get("runtimes") or {}).get(current_runtime) or config["runtimes"]["pi"]
    default = str(existing.get("provider") or current_runtime.removeprefix("pi-") or "openrouter")
    provider = _ask("Pi API provider (openrouter, openai, anthropic, or custom)", default).lower()
    preset = dict(PROVIDER_PRESETS.get(provider) or {})
    if not preset:
        preset = {
            "display_provider": _ask("Provider display name", provider),
            "base_url": _ask("Provider API base URL", str(existing.get("base_url") or "https://api.example.com/v1")),
            "api": _ask("Pi API protocol", str(existing.get("api") or "openai-completions")),
            "key_env": _ask("Credential environment variable", str(existing.get("key_env") or provider.upper() + "_API_KEY")),
        }
    profile = f"pi-{provider}"
    runtime_config = {**config["runtimes"]["pi"], **preset, "provider": provider}
    for key, value in runtime_config.items():
        update_path = f"runtimes.{profile}.{key}"
        from configuration import update_local
        update_local(update_path, value)
    config["runtimes"][profile] = runtime_config
    return profile, runtime_config


def _setup(argv: list[str] | None = None) -> int:
    """First-run wizard. Ask for configuration instead of exposing internals."""
    parser = argparse.ArgumentParser(
        prog="moonshiner setup",
        description="Configure the models, login, and storage Moonshiner will use.")
    parser.add_argument("--reconfigure", action="store_true",
                        help="Ask every setup question again.")
    parser.parse_args(argv or [])
    from configuration import load_config, update_local
    config = load_config()
    harnesses = ["pi", "codex", "claude-code"]
    print("Welcome to Moonshiner. Let's set it up.\n")
    choices = []
    for label, key in (("Trace author", "teacher"), ("Trace judge", "judge")):
        current = config[key]
        current_runtime = current["runtime"]
        default_harness = "pi" if current_runtime.startswith("pi") else current_runtime
        print("Available harnesses: " + ", ".join(harnesses))
        harness = _ask(f"{label} harness", default_harness)
        if harness not in harnesses:
            raise SystemExit(f"Unknown harness {harness!r}. Choose: {', '.join(harnesses)}")
        runtime = harness
        if harness == "pi":
            runtime, _ = _configure_pi_provider(config, current_runtime)
        model = _ask(f"{label} model", current["model"])
        reasoning = str(current.get("reasoning") or "default")
        choices.append((key, runtime, model, reasoning))

    from configuration import PROJECT_STATE
    print(f"This project's config and output stay in {PROJECT_STATE}.\n")
    for key, runtime, model, reasoning in choices:
        update_local(f"{key}.runtime", runtime)
        update_local(f"{key}.model", model)
        update_local(f"{key}.reasoning", reasoning)
    # Seed creation uses the same author/judge unless the user later changes it.
    for source, target in ((choices[0], "seed_author"), (choices[1], "seed_judge")):
        _, runtime, model, reasoning = source
        update_local(f"{target}.runtime", runtime)
        update_local(f"{target}.model", model)
        update_local(f"{target}.reasoning", reasoning)
    update_local("storage.root", str(PROJECT_STATE))
    os.environ["MOONSHINER_HOME"] = str(PROJECT_STATE)

    # Ask for provider keys only when the chosen runtime actually uses one.
    config = load_config()
    from common import key_env_name, key_persist_path
    configured = set()
    for _, runtime, _, _ in choices:
        runtime_config = config["runtimes"][runtime]
        try:
            env_name = key_env_name(runtime_config)
            path = key_persist_path(runtime_config)
        except RuntimeError:
            label = "Claude Code" if runtime == "claude-code" else "Codex"
            print(f"{label}: using its existing CLI login; no API key requested.")
            continue
        provider = str(runtime_config.get("display_provider") or
                       runtime_config.get("provider") or runtime)
        if provider in configured or os.environ.get(env_name) or path.exists():
            continue
        configured.add(provider)
        label = {"openrouter": "OpenRouter", "openai": "OpenAI",
                 "anthropic": "Anthropic", "zai": "Z.ai"}.get(
                     provider.lower(), provider.replace("-", " ").title())
        secret = getpass.getpass(f"{label} API key ({env_name}; hidden): ").strip()
        if not secret:
            raise SystemExit("No API key entered. Run `moonshiner auth set " + provider + "` to finish setup.")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(secret); path.chmod(0o600)
    # The Pi agent is a pinned runtime dependency. Install it on demand inside
    # Moonshiner's own bundle; users should never need to know about npm.
    selected = {runtime for _, runtime, _, _ in choices}
    if any(runtime.startswith("pi") for runtime in selected):
        pi_cli = ROOT / config["runtimes"]["pi"].get("cli", "node_modules/.bin/pi")
        if not pi_cli.exists():
            npm = shutil.which("npm")
            if not npm:
                raise SystemExit("The selected Pi runtime needs Node.js 22 or newer. Install Node.js, then run `moonshiner` again.")
            version = config["runtimes"]["pi"].get("runtime_version", "0.80.7")
            print(f"Installing the Pi runtime {version}…")
            subprocess.run([npm, "install", "--no-audit", "--no-fund", "--prefix",
                            str(ROOT), f"@earendil-works/pi-coding-agent@{version}"],
                           check=True)
    update_local("onboarding.complete", True)
    print("\nSetup complete.\n")
    return 0


def _configured() -> bool:
    from configuration import LOCAL_PATH, load_config
    if not LOCAL_PATH.exists():
        return False
    return bool(load_config().get("onboarding", {}).get("complete"))


def _start_default_queues() -> int:
    """Start every queue enabled for this project; never duplicate a live worker."""
    from common import CONFIG
    queues = ((CONFIG.get("pipeline") or {}).get("queues") or {})
    if queues.get("seed_authoring"):
        running = subprocess.run(
            ["pgrep", "-f", "[m]oonshiner.py behavior-seed author --all"],
            stdout=subprocess.DEVNULL).returncode == 0
        if not running:
            from behavior_seed_pipeline import main as behavior_seed_main
            result = behavior_seed_main(["--all", "--yes", "--detach"])
            if result:
                return result
    if queues.get("tracing", True):
        active = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet",
             "moonshiner-trace-continuous.service"]).returncode == 0
        if not active:
            from common import ROOT, RUNS
            kind = str(queues.get("trace_kind") or "all")
            log_dir = RUNS / "trace-continuous"
            log_dir.mkdir(parents=True, exist_ok=True)
            command = ["systemd-run", "--user", "--collect",
                       "--unit=moonshiner-trace-continuous",
                       f"--property=WorkingDirectory={ROOT}",
                       "--property=Restart=always", "--property=RestartSec=10s",
                       f"--property=StandardOutput=append:{log_dir / 'run.log'}",
                       f"--property=StandardError=append:{log_dir / 'run.log'}",
                       f"--setenv=PATH={os.environ.get('PATH', '')}",
                       "--setenv=MOONSHINER_SUPERVISED=1", sys.executable,
                       str(ROOT / "moonshiner.py"), "run", "--kind", kind,
                       "--all", "--yes"]
            subprocess.run(command, check=True)
    print("Moonshiner queues are running: author (when enabled), trace/judge/retrace, "
          "format/privacy, append, HF upload, and remote verification.")
    return 0


def _storage(argv: list[str]) -> int:
    from common import STORAGE_ROOT
    from configuration import update_local
    parser = argparse.ArgumentParser(prog="moonshiner storage")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    sub.add_parser("set")
    args = parser.parse_args(argv)
    if args.action == "status":
        print(STORAGE_ROOT)
        return 0
    print("Moonshiner storage is tied to the current directory. Change to the "
          "desired project directory and run `moonshiner` there.", file=sys.stderr)
    return 2


def _status(argv: list[str], *, inspect: bool = False) -> int:
    from run_state import connect, job_rows, summaries
    parser = argparse.ArgumentParser(prog=f"moonshiner {'inspect' if inspect else 'status'}")
    parser.add_argument("run_id", nargs="?", help="Run id (latest when omitted for inspect).")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--all", action="store_true", help="Show historical runs.")
    args = parser.parse_args(argv)
    db = connect(); rows = summaries(db, args.run_id)
    if inspect and not args.run_id and rows: args.run_id = rows[0]["id"]; rows = rows[:1]
    if not rows:
        print("no matching runs"); return 1
    if not inspect and not args.run_id and not args.all:
        from behavior_seed_pipeline import status as behavior_status
        from common import DATA, SEEDS_DIR
        from configuration import load_config
        behavior = behavior_status()
        coding = len(list(SEEDS_DIR.glob("*/task.json")))
        wave_counts = {}
        for path in SEEDS_DIR.glob("*/task.json"):
            try:
                wave = (json.loads(path.read_text()).get("catalog") or {}).get("wave")
                if wave is not None:
                    wave_counts[int(wave)] = wave_counts.get(int(wave), 0) + 1
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        active = [row for row in rows if row["status"] == "running" and row["kind"] == "trace"]
        author_runs = [row for row in rows if row["status"] == "running" and row["kind"] == "seed"]
        ack = DATA / "hf-sync" / "published-trajectories.json"
        published = 0
        if ack.is_file():
            try: published = len(json.loads(ack.read_text()).get("published_tasks") or [])
            except (OSError, json.JSONDecodeError): pass
        config = load_config()
        workers = int(((config.get("pipeline") or {}).get("trace") or {}).get("workers", 1))
        units = []
        service_result = subprocess.run(
            ["systemctl", "--user", "list-units", "--type=service", "--state=running",
             "--no-legend", "--plain"], text=True, capture_output=True)
        for line in service_result.stdout.splitlines():
            name = line.split(None, 1)[0] if line.split() else ""
            if name.startswith("moonshiner-"):
                units.append(name.removesuffix(".service"))
        payload = {
            "seeds": {"coding": coding, "behavioral": behavior["total"],
                      "behavioral_authored": behavior["sol_authored"],
                      "behavioral_remaining": behavior["remaining"],
                      "waves": dict(sorted(wave_counts.items()))},
            "authoring": {"active_runs": author_runs},
            "tracing": {"workers": workers, "active_runs": active},
            "publishing": {"dataset": (config.get("publish") or {}).get("hf_dataset"),
                           "batch_size": int((config.get("publish") or {}).get("batch_size", 1)),
                           "published_trajectories": published},
            "services": sorted(units),
        }
        if args.json:
            print(json.dumps(payload, indent=2)); return 0
        print("Moonshiner status")
        print(f"Seeds: {coding} coding; {behavior['sol_authored']}/{behavior['total']} "
              f"behavioral authored ({behavior['remaining']} remaining)")
        if wave_counts:
            print("Waves: " + ", ".join(f"{wave}={count}" for wave, count in sorted(wave_counts.items())))
        print(f"Authoring: {len(author_runs)} coding seed(s) in progress; behavioral authoring "
              f"{'complete' if behavior['remaining'] == 0 else 'active'}")
        print(f"Tracing: {workers} workers configured; {len(active)} active run(s)")
        for row in active:
            print(f"  {row['id']}: accepted={row.get('accepted') or 0}, "
                  f"failed={row.get('failed') or 0}, pending={row.get('pending') or 0}")
        print(f"Publishing: {published} trajectories acknowledged; batch size "
              f"{payload['publishing']['batch_size']}; {payload['publishing']['dataset'] or 'disabled'}")
        print("Services: " + (", ".join(sorted(units)) if units else "none"))
        return 0
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


def _help() -> str:
    return """Moonshiner creates reviewed coding-agent traces and training datasets.

START
  moonshiner                 Configure once, then start all enabled project queues
  moonshiner setup           Change models or authentication for this directory
  moonshiner doctor          Check that everything is ready

CREATE TRAINING DATA
  moonshiner run --limit 20 --yes
                              Create and review traces for 20 seeds
  moonshiner trace import --directory PATH
                              Resume from existing traces or prepared rows
  moonshiner trace import --hf OWNER/DATASET
                              Resume from a Hugging Face dataset
  moonshiner status           Show current and previous runs
  moonshiner dataset build    Build a dataset from accepted traces

AUTHOR SEEDS
  moonshiner seed run --id NAME --brief "WHAT TO BUILD" --yes
                              Author, test, review, and save one new seed
  moonshiner behavior-seed author --all --yes
                              Author only the non-code behavioral curriculum
  moonshiner seeds catalog    Browse the seed recipe book

CONFIGURE
  moonshiner config show      Show the current configuration
  moonshiner auth set PROVIDER
                              Save a provider key, such as openrouter
  moonshiner storage status  Show this directory's .moonshiner storage path

Run `moonshiner COMMAND --help` for details. Advanced internals are under
`moonshiner pipeline --help` and are not needed for normal use."""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--version"]:
        print(f"moonshiner {VERSION}"); return 0
    if argv and argv[0] in ("-h", "--help", "help"):
        print(_help())
        return 0

    # Every operational invocation establishes the current directory as an
    # explicit, independent project boundary before setup or output begins.
    from configuration import confirm_project
    if not confirm_project():
        return 1
    if not argv:
        if not _configured():
            result = _setup([])
            if result: return result
        return _start_default_queues()
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
    if command == "setup":
        return _setup(rest)
    if command == "storage":
        return _storage(rest)
    if command in {"run", "trace", "trace-run"}:
        if command == "trace" and rest and rest[0] == "import":
            from import_existing import main as import_main
            return import_main(rest[1:])
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
    if command == "seeds":
        from corpus import main as corpus_main
        return corpus_main(rest)
    if command == "publish":
        from publish import main as publish_main
        return publish_main(rest)
    if command in {"seed", "seed-run"}:
        if command == "seed" and rest and rest[0] == "run": rest = rest[1:]
        from seed_pipeline import main as seed_main
        return seed_main(rest)
    if command == "behavior-seed":
        if rest and rest[0] == "author":
            from behavior_seed_pipeline import main as behavior_seed_main
            return behavior_seed_main(rest[1:])
        if rest == ["status"]:
            from behavior_seed_pipeline import status as behavior_seed_status
            print(json.dumps(behavior_seed_status(), indent=2)); return 0
        print("usage: moonshiner behavior-seed {author,status}",
              file=sys.stderr)
        return 2
    if command == "dataset":
        if rest and rest[0] in {"compose", "prepare"}:
            from dataset_prep import main as dataset_main
            return dataset_main(rest)
        if not rest or rest[0] not in {"build", "export"}:
            print("usage: moonshiner dataset {build,export,compose,prepare}", file=sys.stderr)
            return 2
        return _run(["--from", "build"])
    if command in BY_KEY:
        return _dispatch(BY_KEY[command], rest)

    print(f"[moonshiner] unknown command: {command}", file=sys.stderr)
    print("Run `moonshiner --help` to see the available commands.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
