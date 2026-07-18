"""Shared core for the moonshiner distillation harness.

Single source of truth for repository paths, configuration, seed loading and
materialization, verification, protected-file hashing, workspace diffing,
output scrubbing, and the portable student system prompt. Teacher- and
judge-runtime specifics (Claude Code, Codex, Pi/GLM) live in ``src/runtimes``;
everything runtime-agnostic lives here so one pipeline can distill any model.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_config() -> dict:
    """Load config.json, shallow-merging an optional config.local.json over it."""
    config = json.loads((ROOT / "config.json").read_text())
    local = ROOT / "config.local.json"
    if local.exists():
        overrides = json.loads(local.read_text())
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            else:
                config[key] = value
    return config


CONFIG = _load_config()
SEEDS_DIR = ROOT / "tasks" / "seeds"
WORKSPACES = ROOT / "workspaces"
TRACES = ROOT / "traces"
DATA = ROOT / "data"
RUNS = ROOT / "runs"
QUARANTINE_DIR = TRACES / "quarantine"

# Explicit acknowledgement required before any metered teacher/judge call. The
# claude-code and codex accounts bill real credits; pi routes through a paid
# provider key (Z.ai, OpenRouter, …). Guarded launchers refuse to run without
# this exact value.
PAID_RUN_UNLOCK = "CREDITS_PURCHASED"

# The portable contract baked into every training row's system turn. Whatever
# harness eventually serves the distilled student should reuse this verbatim —
# the train/deploy prompt match is part of why the fine-tune transfers.
SYSTEM_PROMPT = """You are an autonomous coding agent working in the current directory of a real repository on the user's machine.

Use your tools to read, search, create, and edit files and to run shell commands (tests, builds, type-checkers). Some tools may be deferred (only their names are known until you load their schemas) and some may be unavailable or offline; if a tool call fails, adapt and continue with what is available. Never guess at the contents of a file you have not read, and never claim a fix works without running the verification yourself.

Method:
1. Reproduce first. Read the relevant code and run the failing command before changing anything.
2. Form a hypothesis about the root cause; make the smallest edit that tests it.
3. Verify. Rerun the tests/build after every meaningful change. If it still fails, re-read the output carefully — do not repeat the same edit.
4. Fix the cause, not the symptom.

Rules:
- Never modify tests merely to make them pass, unless the user explicitly says the tests are wrong.
- Keep every read, write, fixture, probe, and shell working directory inside the current repository; do not use /tmp, /var/tmp, $HOME, a sibling repository, or any other external path.
- Do not install global software or mutate Git state with commit, stash, reset, checkout, or clean.
- Keep edits minimal and consistent with the existing code style.
- End with a brief summary: the root cause, what you changed, and proof that it passes."""

# Secret shapes dropped from any exported row. Kept broad on purpose.
SECRET_RE = re.compile(
    r"(sk-(?:proj-)?[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{8,}"
    r"|AKIA[0-9A-Z]{16}|gh[opusr]_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
# Disposable per-turn runtime directories created by the Pi/security sandboxes.
RUNTIME_PATH_RE = re.compile(
    r"/var/tmp/moonshiner-(?:pi|security)-runtime/(?:run|probe-run)-[A-Za-z0-9._-]+"
)


# --------------------------------------------------------------------------- #
# Provider credentials — PER PROVIDER, so several keyed runtimes can coexist  #
# in one run. Each keyed runtime derives its own env var and staged file      #
# from its `provider`; explicit `key_env`/`key_file_name` override.           #
# --------------------------------------------------------------------------- #
def _provider_slug(runtime_config: dict) -> str:
    """A filesystem/env-safe slug of the runtime's provider, or raise."""
    provider = str((runtime_config or {}).get("provider") or "").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", provider.lower()).strip("-")
    if not slug:
        raise RuntimeError(
            "runtime config names no provider: set 'provider' (or an explicit "
            "'key_env'/'key_file_name') so its credential cannot be confused "
            "with another provider's")
    return slug


def key_env_name(runtime_config: dict) -> str:
    """The env var holding this runtime's provider key (<PROVIDER>_API_KEY)."""
    explicit = str((runtime_config or {}).get("key_env") or "").strip()
    if explicit:
        return explicit
    return _provider_slug(runtime_config).replace("-", "_").upper() + "_API_KEY"


def key_file_path(runtime_config: dict) -> Path:
    """This runtime's staged key file under $XDG_RUNTIME_DIR.

    Defaults to ``moonshiner-<provider>-key`` so two providers never share a
    file; ``key_file_name`` overrides. ``scripts/stage_key.sh`` writes it.
    """
    name = str((runtime_config or {}).get("key_file_name") or "").strip()
    if not name:
        name = f"moonshiner-{_provider_slug(runtime_config)}-key"
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(xdg) if xdg else Path(f"/run/user/{os.getuid()}")
    return base / name


def provider_key_env_names(config: dict | None = None) -> tuple[str, ...]:
    """Every configured keyed runtime's key env name, for redaction gates."""
    names: list[str] = []
    for runtime_config in ((config or CONFIG).get("runtimes") or {}).values():
        if not isinstance(runtime_config, dict):
            continue
        try:
            name = key_env_name(runtime_config)
        except RuntimeError:          # not a keyed provider (OAuth runtimes)
            continue
        if name not in names:
            names.append(name)
    return tuple(names)


@functools.lru_cache(maxsize=1)
def _staged_secret_values() -> tuple[str, ...]:
    """Contents of every configured runtime's staged key file, for redaction."""
    values: list[str] = []
    for runtime_config in (CONFIG.get("runtimes") or {}).values():
        if not isinstance(runtime_config, dict) or not runtime_config:
            continue
        try:
            staged = key_file_path(runtime_config).read_text().strip()
        except (RuntimeError, OSError):
            continue
        if staged and staged not in values:
            values.append(staged)
    return tuple(values)

# Runtime-only artifacts: excluded from candidate diffs and cleaned before an
# independent screening replay. Verification can recreate them after the agent
# has already cleaned its workspace, and their binary diffs do not replay.
RUNTIME_CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache",
                          ".ruff_cache", "node_modules"}
RUNTIME_CACHE_SUFFIXES = {".pyc", ".pyo"}
DIFF_EXCLUDE_PATTERNS = (
    "**/__pycache__/**", "**/*.pyc", "**/*.pyo", "**/.pytest_cache/**",
    "**/.mypy_cache/**", "**/.ruff_cache/**", "node_modules/**",
    ".venv/**", "env/**", "target/**", "**/bin/**", "**/obj/**",
)


# --------------------------------------------------------------------------- #
# OpenAI-style tool-schema helpers (used by runtime adapters + build_dataset)  #
# --------------------------------------------------------------------------- #
def fn(name: str, description: str, properties: dict | None = None,
       required: list | None = None) -> dict:
    """Build one OpenAI-compatible function tool schema."""
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
    }}


def stub(name: str,
         description: str = "Tool observed in a teacher trace; load its schema "
                            "with ToolSearch before calling it.") -> dict:
    """Represent a trace tool whose detailed schema is not modeled."""
    schema = fn(name, description)
    schema["function"]["parameters"]["additionalProperties"] = True
    return schema


def schemas_for(names, registry: dict, warn: list | None = None) -> list:
    """Map tool names to schemas from ``registry``, auto-stubbing unknowns."""
    out = []
    for name in names:
        if name in registry:
            out.append(registry[name])
        else:
            if warn is not None:
                warn.append(name)
            out.append(stub(name))
    return out


# --------------------------------------------------------------------------- #
# Quarantine (fail-closed training exclusions)                                 #
# --------------------------------------------------------------------------- #
def active_quarantines(directory: Path | None = None) -> list[dict]:
    directory = Path(directory) if directory is not None else QUARANTINE_DIR
    records = []
    if not directory.is_dir():
        return records
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"invalid quarantine record {path}: {error}") from error
        if record.get("status") in {"replacement_required", "training_excluded"}:
            records.append(record)
    return records


def quarantined_tasks(directory: Path | None = None) -> set[str]:
    return {record["task"] for record in active_quarantines(directory)
            if isinstance(record.get("task"), str)}


def quarantined_trajectories(directory: Path | None = None) -> set[str]:
    excluded: set[str] = set()
    for record in active_quarantines(directory):
        excluded.update(value for value in record.get("excluded_trajectory_ids", [])
                        if isinstance(value, str))
    return excluded


# --------------------------------------------------------------------------- #
# Seeds                                                                        #
# --------------------------------------------------------------------------- #
def load_seeds(only: set[str] | None = None, include_holdout: bool = False) -> list[dict]:
    """Load tasks/seeds/*/task.json, sorted by seed id.

    A seed being written by a concurrent author (invalid JSON mid-write) is
    skipped with a warning rather than crashing a batch.
    """
    import sys
    seeds = []
    holdouts = set(CONFIG.get("holdout_tasks", []))
    for task_json in sorted(SEEDS_DIR.glob("*/task.json")):
        try:
            seed = json.loads(task_json.read_text())
        except json.JSONDecodeError:
            print(f"warning: {task_json} is invalid JSON (mid-write?); skipped",
                  file=sys.stderr)
            continue
        seed["_dir"] = task_json.parent
        if only and seed["id"] not in only:
            continue
        if not include_holdout and seed["id"] in holdouts:
            continue
        seeds.append(seed)
    return seeds


def seed_fingerprint(seed: dict) -> str:
    """Hash the canonical task definition and every shipped workspace file.

    Length-prefixing each relative path and payload makes the digest immune to
    boundary ambiguities, so a stale review is detected if any byte changes.
    """
    digest = hashlib.sha256()
    task_path = seed["_dir"] / "task.json"
    for path in [task_path, *sorted((seed["_dir"] / "files").rglob("*"))]:
        if not path.is_file():
            continue
        relative = path.relative_to(seed["_dir"]).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def materialize(seed: dict, name: str | None = None) -> Path:
    """Copy a seed's files into a fresh, committed Git workspace.

    A top-level ``node_modules`` is treated as an installed cache and skipped;
    fixtures deliberately vendored below e.g. ``vendor/node_modules`` are part
    of the task baseline and preserved.
    """
    workspace = WORKSPACES / (name or seed["id"])
    if workspace.resolve().parent != WORKSPACES.resolve():
        raise ValueError(f"unsafe workspace id: {seed['id']!r}")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    source = seed["_dir"] / "files"
    if source.exists():
        source_root = source.resolve()

        def ignore_runtime_caches(directory, names):
            ignored = {name for name in names
                       if name in {"__pycache__", ".pytest_cache"}
                       or Path(name).suffix in RUNTIME_CACHE_SUFFIXES}
            if Path(directory).resolve() == source_root and "node_modules" in names:
                ignored.add("node_modules")
            return ignored

        shutil.copytree(source, workspace, dirs_exist_ok=True,
                        ignore=ignore_runtime_caches)
    git = ["git", "-c", "user.email=harness@moonshiner",
           "-c", "user.name=moonshiner harness"]
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    # Seed files are authoritative fixtures even when a nested path uses a
    # conventional ignore name such as vendor/node_modules.
    subprocess.run(git + ["add", "-A", "-f"], cwd=workspace, check=True)
    subprocess.run(git + ["commit", "-qm", "baseline", "--allow-empty"],
                   cwd=workspace, check=True)
    return workspace


def run_setup(seed: dict, workspace: Path) -> tuple[bool, str]:
    """Run a seed's declared dependency/setup preparation (if any)."""
    command = seed.get("reference_setup")
    if not command:
        return True, "(no reference_setup)"
    try:
        proc = subprocess.run(shlex.split(command), cwd=workspace,
                              capture_output=True, text=True, timeout=600)
        return proc.returncode == 0, (proc.stdout + "\n" + proc.stderr).strip()[-2000:]
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)


def run_verify(seed: dict, workspace: Path, timeout: int | None = None
               ) -> tuple[bool | None, str]:
    """Run a seed's verification command; return (passed|None, output).

    ``verify_timeout`` may raise the default 180s for install-heavy seeds, but
    is capped at 360s per the sanctioned spec.
    """
    if not seed.get("verify_cmd"):
        return None, "(no verify_cmd)"
    if timeout is None:
        timeout = min(int(seed.get("verify_timeout") or 180), 360)
    try:
        proc = subprocess.run(shlex.split(seed["verify_cmd"]), cwd=workspace,
                              capture_output=True, text=True, timeout=timeout)
        return proc.returncode == 0, (proc.stdout + "\n" + proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, f"(verify timed out after {timeout}s)"
    except FileNotFoundError as exc:
        return False, f"(verify toolchain missing: {exc})"


def protected_hashes(seed: dict, workspace: Path) -> dict[str, str | None]:
    """Hash protected files so traces that modify tests can be rejected."""
    hashes = {}
    for relative in seed.get("test_files", []):
        path = workspace / relative
        hashes[relative] = (hashlib.sha256(path.read_bytes()).hexdigest()
                            if path.exists() else None)
    return hashes


# Historical alias — both names appear across the source harnesses.
test_file_hashes = protected_hashes


def git_diff(workspace: Path) -> str:
    """Full diff vs baseline, excluding runtime caches and build artifacts."""
    subprocess.run(["git", "add", "-A", "-N"], cwd=workspace, capture_output=True)
    command = ["git", "diff", "--binary", "HEAD", "--", "."]
    command.extend(f":(exclude,glob){pattern}" for pattern in DIFF_EXCLUDE_PATTERNS)
    proc = subprocess.run(command, cwd=workspace, capture_output=True, text=True)
    return proc.stdout


def clear_runtime_caches(workspace: Path) -> list[str]:
    """Remove verifier-created caches without touching candidate source.

    A tracked (vendored) ``node_modules`` is preserved; only untracked caches
    and known runtime suffixes are removed.
    """
    tracked_directories: set[str] = set()
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=workspace,
                             capture_output=True)
    if tracked.returncode == 0:
        for value in tracked.stdout.decode(errors="surrogateescape").split("\0"):
            if not value:
                continue
            parent = Path(value).parent
            while parent != Path("."):
                tracked_directories.add(parent.as_posix())
                parent = parent.parent

    removed = []
    for root, directories, files in os.walk(workspace, topdown=True):
        directory = Path(root)
        for name in list(directories):
            if name not in RUNTIME_CACHE_DIR_NAMES:
                continue
            path = directory / name
            relative = path.relative_to(workspace).as_posix()
            if name == "node_modules" and relative in tracked_directories:
                continue
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path)
            directories.remove(name)
            removed.append(relative)
        for name in files:
            path = directory / name
            if path.suffix not in RUNTIME_CACHE_SUFFIXES:
                continue
            path.unlink()
            removed.append(path.relative_to(workspace).as_posix())
    return sorted(removed)


def scrub_text(value: str, workspace: str | None = None) -> str:
    """Rewrite machine paths to portable placeholders and redact secrets."""
    value = value.replace("\x00", "")
    if workspace:
        value = value.replace(workspace + "/", "").replace(workspace, ".")
    value = value.replace(str(ROOT), "/repo")
    value = value.replace(str(Path.home()), "~")
    value = RUNTIME_PATH_RE.sub("/runtime", value)
    static_names = ("ZAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "OPENROUTER_API_KEY", "HF_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
    for env_name in dict.fromkeys(static_names + provider_key_env_names()):
        secret = os.environ.get(env_name)
        if secret:
            value = value.replace(secret, "[REDACTED_SECRET]")
    for secret in _staged_secret_values():
        value = value.replace(secret, "[REDACTED_SECRET]")
    return SECRET_RE.sub("[REDACTED_SECRET]", value).strip()
