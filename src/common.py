"""Shared core for the moonshiner distillation harness.

Single source of truth for repository paths, configuration, seed loading and
materialization, verification, protected-file hashing, workspace diffing,
output scrubbing, and catalog metadata. Teacher- and
judge-runtime specifics (Claude Code, Codex, Pi/GLM) live in ``src/runtimes``;
everything runtime-agnostic lives here so one pipeline can distill any model.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

ROOT = Path(os.environ.get("MOONSHINER_BUNDLE_ROOT",
                           Path(__file__).resolve().parent.parent)).resolve()


def _storage_root() -> Path:
    from configuration import PROJECT_STATE
    return PROJECT_STATE


STORAGE_ROOT = _storage_root()


def model_workspace_root(*, project_root: Path | None = None,
                         data_home: Path | None = None) -> Path:
    """Return the project-scoped model workspace root outside the project.

    Model CLIs discover instruction files by walking upward from their current
    working directory.  Project state commonly lives at ``.moonshiner`` below
    the checkout, so it must never also be used as a model workspace root.
    """
    if project_root is None:
        from configuration import PROJECT_ROOT
        project_root = PROJECT_ROOT
    project = Path(project_root).resolve()
    base = Path(data_home or os.environ.get(
        "XDG_DATA_HOME", Path.home() / ".local" / "share")).expanduser().resolve()
    project_key = hashlib.sha256(str(project).encode()).hexdigest()[:12]
    root = (base / "moonshiner" / "projects" / project_key / "workspaces").resolve()
    if root == project or project in root.parents:
        raise RuntimeError(
            f"model workspace root must be outside the project repository: {root}")
    return root


def _load_config() -> dict:
    """Load built-in, user, then repository-local configuration layers."""
    from configuration import load_config
    return load_config()


CONFIG = _load_config()
_installed_seeds = STORAGE_ROOT / "corpora" / "active" / "tasks" / "seeds"
_bundled_seeds = ROOT / "tasks" / "seeds"
def _corpus_version(root: Path) -> str:
    try:
        return str(json.loads((root / "corpus-version.json").read_text())["version"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return "0"

_authoring_enabled = bool((((CONFIG.get("pipeline") or {}).get("queues") or {})
                           .get("seed_authoring")))
_active_root = STORAGE_ROOT / "corpora" / "active"
def prefer_active_corpus(installed: bool, authoring: bool,
                         active_version: str, bundled_version: str) -> bool:
    return installed and (authoring or active_version >= bundled_version)

_use_active = prefer_active_corpus(
    _installed_seeds.is_dir(), _authoring_enabled,
    _corpus_version(_active_root), _corpus_version(ROOT))
_corpus_seeds = _installed_seeds if _use_active else _bundled_seeds


def _authoring_seeds_dir() -> Path | None:
    """The clone's seed directory, once it exists.

    Authoring writes accepted seeds straight into the repository, so the clone
    is authoritative whenever it is present. Resolution stays read-only: the
    clone is created by ``seed_repo.ensure`` at authoring startup, never as an
    import side effect of an unrelated command.
    """
    if not _authoring_enabled:
        return None
    from seed_repo import seeds_dir
    candidate = seeds_dir(CONFIG)
    return candidate if candidate.is_dir() else None


SEEDS_DIR = _authoring_seeds_dir() or _corpus_seeds
BEHAVIOR_WORLDS = (SEEDS_DIR.parent / "behavior-worlds.json"
                   if (SEEDS_DIR.parent / "behavior-worlds.json").is_file()
                   else ROOT / "tasks" / "behavior-worlds.json")
WORKSPACES = model_workspace_root()
# The verify sandbox needs writable state -- a HOME, a temporary directory,
# shared memory, and the mount points for what the harness provides. None of it
# may live in the workspace: a seed's verifier walks its project directory to
# judge what the agent left there, and 152 seeds in this corpus fail outright
# on finding `.sandbox-home` beside their own files. Kept a sibling of the
# workspace root so it is still moonshiner-owned state, removed with the
# workspace it belongs to, and masked out of the model sandbox like any other
# peer state.
VERIFY_HOMES = WORKSPACES.parent / "verify-homes"
TRACES = STORAGE_ROOT / "traces"
DATA = STORAGE_ROOT / "data"
RUNS = STORAGE_ROOT / "runs"
QUARANTINE_DIR = TRACES / "quarantine"

# Explicit acknowledgement required before any metered teacher/judge call. The
# claude-code and codex accounts bill real credits; pi routes through a paid
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


def key_persist_path(runtime_config: dict) -> Path:
    """This runtime's persistent key file under ``$XDG_CONFIG_HOME/moonshiner``.

    The staged tmpfs file clears on reboot; this one does not. Same file name
    as the staged copy. ``scripts/stage_key.sh`` writes both.
    """
    name = str((runtime_config or {}).get("key_file_name") or "").strip()
    if not name:
        name = f"moonshiner-{_provider_slug(runtime_config)}-key"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "moonshiner" / name


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


def _staged_secret_values() -> tuple[str, ...]:
    """Contents of every runtime's staged and persistent key files, for redaction."""
    values: list[str] = []
    for runtime_config in (CONFIG.get("runtimes") or {}).values():
        if not isinstance(runtime_config, dict) or not runtime_config:
            continue
        for path_of in (key_file_path, key_persist_path):
            try:
                secret = path_of(runtime_config).read_text().strip()
            except (RuntimeError, OSError):
                continue
            if secret and secret not in values:
                values.append(secret)
    return tuple(values)

# Backward-compatible test/extension hook; values are intentionally never cached.
_staged_secret_values.cache_clear = lambda: None

# Runtime-only artifacts: excluded from candidate diffs and cleaned before an
# independent screening replay. Verification can recreate them after the agent
# has already cleaned its workspace, and their binary diffs do not replay.
# .sandbox-home is the throwaway HOME the sandbox points a run at, and
# .toolchain holds the cargo and rustup state a run provisions for itself. It is
# created fresh for every run and is never seed content, but pwsh, go, cargo
# and dconf all write caches and telemetry into it, which made a verified
# workspace look dirty and rejected otherwise sound seeds.
RUNTIME_CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache",
                          ".ruff_cache", "node_modules", ".sandbox-home",
                          ".toolchain"}
# `.class` is here for the same reason as `.pyc`: `javac` is how a Java seed's
# verify command runs at all, and the class files it drops beside the sources
# then read as the agent having left build output behind. Sixteen seeds failed
# their post-reversal cleanliness check on artifacts the verifier itself made.
RUNTIME_CACHE_SUFFIXES = {".pyc", ".pyo", ".class"}
DIFF_EXCLUDE_PATTERNS = (
    "**/.sandbox-home/**", "**/.toolchain/**",
    "**/__pycache__/**", "**/*.pyc", "**/*.pyo", "**/.pytest_cache/**",
    "**/.mypy_cache/**", "**/.ruff_cache/**", "node_modules/**",
    ".venv/**", "env/**", "target/**", "**/bin/**", "**/obj/**",
)


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
    """Load every authored seed in catalog priority order.

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
    installed_catalog = SEEDS_DIR.parents[1] / "SEED_CATALOG.json"
    catalog_path = (installed_catalog if installed_catalog.is_file()
                    else ROOT / "SEED_CATALOG.json")
    try:
        catalog = json.loads(catalog_path.read_text())
        programs = catalog.get("programs") or {}
        catalog_items = {
            item["id"]: item
            for items in (catalog.get("categories") or {}).values()
            for item in items
        }
        rank = {item["id"]: (int(programs.get(item.get("program"), {}).get(
                    "priority", 1_000_000)), position)
                for position, item in enumerate(
                    entry for items in (catalog.get("categories") or {}).values()
                    for entry in items)}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        rank, catalog_items = {}, {}
    for seed in seeds:
        seed["_catalog_program"] = (
            catalog_items.get(seed["id"]) or {}).get("program")
    return sorted(seeds, key=lambda seed: (*rank.get(seed["id"], (1_000_000, 0)),
                                           seed["id"]))


def select_seeds(*, only: set[str] | None = None,
                 categories: set[str] | None = None,
                 tags: set[str] | None = None, name: str | None = None,
                 include_holdout: bool = True) -> list[dict]:
    """Select catalog recipes consistently for tracing and user inspection."""
    selected = load_seeds(only=only, include_holdout=include_holdout)
    needle = (name or "").casefold()
    return [seed for seed in selected
            if (not categories or seed.get("category") in categories)
            and (not tags or tags <= set(seed.get("training_tags") or seed.get("tags") or []))
            and (not needle or needle in seed["id"].casefold()
                 or needle in str(seed.get("prompt") or "").casefold())]


def uses_tool_interaction(seed: dict) -> bool:
    """Identify legacy seeds that embed the removed synthetic tool harness.

    This predicate is retained only so old artifacts can be identified and
    excluded.  It must never select a tracing or publishing implementation.
    Every new trace runs through the configured agent runtime.
    """
    return (isinstance(seed.get("available_tools"), list)
            and isinstance(seed.get("initial_state"), dict)
            and isinstance(seed.get("expected"), dict))


def synthetic_tool_contract(seed: dict) -> str | None:
    """Explain why a legacy seed cannot be executed as a genuine agent trace."""
    fields = [name for name in ("tool_results", "initial_state", "failure_injections")
              if name in seed]
    if fields:
        return "embedded synthetic tool contract: " + ", ".join(fields)
    return None


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


def _seed_files(seed: dict) -> Path | None:
    """Return repository fixtures when the catalog entry has them."""
    directory = seed.get("_dir")
    return Path(directory) / "files" if directory is not None else None


def jsonl_lines(path: Path, *, errors: str | None = None) -> list[str]:
    """Split a JSONL file on newlines only, returning non-empty lines.

    ``str.splitlines`` breaks on every Unicode line boundary — U+2028, U+2029,
    \x0b, \x85 and more — all of which appear legitimately inside JSON string
    values. Using it on JSONL tears objects in half and the parse fails on
    valid data: one imported corpus split into 2273 fragments instead of 2201
    rows. JSONL is newline-delimited and nothing else.
    """
    text = path.read_text(errors=errors) if errors else path.read_text()
    return [line for line in text.split("\n") if line.strip()]


def verify_scratch(workspace: Path, *, workspaces: Path | None = None) -> Path:
    """The writable state a workspace's verify sandbox runs against.

    Paired with the workspace by name -- ``materialize`` already makes those
    unique -- so a seed's ``reference_setup`` and its verify command share one
    HOME and a package one installs is there for the other to use.
    """
    root = (workspaces if workspaces is not None else WORKSPACES).resolve()
    return root.parent / "verify-homes" / Path(workspace).resolve().name


def remove_workspace(path: Path, *, workspaces: Path | None = None) -> None:
    """Delete a materialized workspace. Refuse anything that is not one.

    A workspace is the only thing this project ever deletes. The repository,
    project state, a caller's mistake, a test double handing back the wrong
    path — all of it must fail loudly rather than be removed. Deleting the
    wrong tree is unrecoverable, so the check is a hard precondition rather
    than a best-effort filter, and it is deliberately the single door every
    workspace removal goes through.
    """
    target = Path(path)
    root = (workspaces if workspaces is not None else WORKSPACES).resolve()
    try:
        resolved = target.resolve()
    except OSError as error:
        raise ValueError(f"refusing to remove an unresolvable path: {target}") from error
    if resolved == root or root not in resolved.parents:
        raise ValueError(
            f"refusing to remove a path outside {root}: {resolved}")
    if target.is_symlink():
        raise ValueError(f"refusing to remove a symlinked workspace: {target}")
    if not resolved.exists():
        return
    # Verifier toolchains write read-only trees (Go's module cache) and a
    # sandboxed agent can leave behind a directory it made unreadable to
    # itself -- OpenCode leaves `tmp/opencode/hide` at mode 111. `rmtree`
    # cannot open such a directory to walk it, and the handler below is then
    # called with `os.open`, which takes flags it was not given: the removal
    # died on a TypeError that hid the permission error underneath. So clear
    # the bits on the way down, before anything tries to read them. Symlinks
    # are stepped over rather than followed -- a chmod through one would land
    # outside the workspace.
    stack = [resolved]
    while stack:
        entry = stack.pop()
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            entry.chmod(0o700)
        except OSError:
            pass
        try:
            stack.extend(entry.iterdir())
        except OSError:
            continue

    def force_writable(function, failed, _excinfo):
        for item in (Path(failed).parent, Path(failed)):
            try:
                item.chmod(0o700)
            except OSError:
                pass
        try:
            function(failed)
        except TypeError:
            # `os.open` cannot be retried with a path alone. Say nothing and
            # let the real failure surface, rather than replacing it.
            pass

    shutil.rmtree(resolved, onexc=force_writable)
    # The verify sandbox's state lives outside the workspace so a verifier
    # never sees it; it still belongs to the workspace and goes with it.
    scratch = verify_scratch(resolved, workspaces=workspaces)
    if scratch.is_dir() and not scratch.is_symlink():
        shutil.rmtree(scratch, onexc=force_writable)


def stage_workspace(source: Path, name: str) -> Path:
    """Copy an archived review input into an isolated model workspace."""
    source = Path(source).resolve()
    workspace = WORKSPACES / f"{name}-{uuid.uuid4().hex[:10]}"
    if workspace.resolve().parent != WORKSPACES.resolve():
        raise ValueError(f"unsafe staged workspace name: {name!r}")
    links = [path for path in source.rglob("*") if path.is_symlink()]
    if links:
        raise ValueError(f"review input contains prohibited symlink: {links[0]}")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, workspace)
    return workspace


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
        workspace = WORKSPACES / f"{workspace.name}-{uuid.uuid4().hex[:10]}"
    workspace.mkdir(parents=True)
    from task_environment import environment_spec, materialize_environment
    if environment_spec(seed) is not None:
        materialize_environment(seed, workspace)
        source = None
    else:
        source = _seed_files(seed)
    if source is not None and source.exists():
        links = [path for path in source.rglob("*") if path.is_symlink()]
        if links:
            raise ValueError(f"seed contains prohibited symlink: {links[0]}")
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
    """Run a seed's declared dependency/setup preparation (if any).

    This is the step that fetches what the task needs -- `go mod download`,
    `pip install -r requirements.txt` -- so it is the one step that gets the
    network. It ran offline until now, which made twenty seeds unsolvable by
    construction: the preparation their own reference solution depends on could
    not complete, and the failure read as a broken seed. Verification stays
    offline; it shares this HOME, so what is fetched here is there for it.
    """
    command = seed.get("reference_setup")
    if not command:
        return True, "(no reference_setup)"
    try:
        from toolchains import declared_powershell_modules
        proc = _sandboxed_command(shlex.split(command), workspace, 600,
                                  network=True,
                                  powershell_modules=declared_powershell_modules(seed))
        return proc.returncode == 0, (proc.stdout + "\n" + proc.stderr).strip()[-2000:]
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)


# A seed's acceptance line is a shell line: it is written in the task prompt as
# something an operator would type, and the agent runs it in a shell. Splitting
# it into an argv and executing that directly hands `&&` to the program as an
# argument, so `go vet ./... && go test ./...` reports `malformed import path
# "&&"` and the seed can never pass however good the answer is. Seventeen seeds
# in the corpus are written that way; each burned every attempt it had. Only a
# command that actually needs a shell gets one, so nothing else changes.
SHELL_OPERATORS = frozenset({"&&", "||", ";", "|", "&", ">", ">>", "<", "<<"})


def verify_argv(verify_cmd: str) -> list[str]:
    """Argv for a seed's verify command, through a shell only when it needs one."""
    try:
        argv = shlex.split(verify_cmd)
    except ValueError:  # unbalanced quoting: the shell's problem to report
        argv = []
    needs_shell = (
        not argv
        or any(token in SHELL_OPERATORS for token in argv)
        # A leading assignment (`cache=$(mktemp -d) && ...`) is shell syntax,
        # not a program name.
        or ("=" in argv[0] and not argv[0].startswith("/")))
    return ["/bin/sh", "-c", verify_cmd] if needs_shell else argv


# A verify command is model-authored code, so it runs with no network at all.
# But `dotnet test` and `go test` restore their own dependencies, and the HOME
# they restore into is created empty for every workspace -- so 49 C# seeds
# failed on `NU1301: Network is unreachable` whatever the patch said, and their
# baseline check "failed" for the wrong reason as well. Fetch what the project
# on disk declares once, with the network up, into the same HOME the offline
# verify will then use. Nothing here reads seed-authored commands: the trigger
# is a project file and the command is this harness's own.
DEPENDENCY_WARMUPS = (
    ("dotnet", ("*.sln", "*.slnx", "*.csproj", "*.fsproj", "*.vbproj"),
     ["dotnet", "restore"]),
    ("go", ("go.mod",), ["go", "mod", "download"]),
    # Maven's own `dependency:go-offline` is not enough: surefire picks its
    # provider jar when it runs, and that download is the one the offline
    # verify then fails on. Running the phase the seed will run is what
    # actually fetches what the seed will need.
    ("mvn", ("pom.xml",), ["mvn", "-B", "-q", "test"]),
)


def _declared_dependencies(workspace: Path) -> tuple[list, str]:
    """The warmable projects on disk, and a digest of what they declare."""
    found, digest = [], hashlib.sha256()
    for tool, patterns, command in DEPENDENCY_WARMUPS:
        manifests = sorted(path for pattern in patterns
                           for path in workspace.rglob(pattern) if path.is_file())
        if not manifests or shutil.which(tool) is None:
            continue
        found.append((tool, command))
        for path in manifests:
            digest.update(path.relative_to(workspace).as_posix().encode())
            digest.update(path.read_bytes())
    return found, digest.hexdigest()


def warm_dependency_cache(workspace: Path) -> str:
    """Populate the verify sandbox's package cache while the network is up.

    Re-warmed whenever what the project declares changes, not once per
    workspace: a seed's `reference_fix.patch` may be the thing that introduces
    the build file at all -- `java-bakeplan` has no `pom.xml` until it is
    applied -- so a single warm at baseline would leave the run that matters
    with a cold cache and no network to fill it.
    """
    marker = verify_scratch(workspace) / "dependencies-warmed"
    warmups, declared = _declared_dependencies(workspace)
    try:
        head, *notes = marker.read_text().splitlines()
    except (OSError, ValueError):
        head, notes = "", []
    state, _, counted = head.partition(" ")
    attempts = int(counted) if counted.isdigit() else 0
    # A warm-up against a deliberately broken baseline stops where the build
    # does, so what a build needs only once it compiles stays unfetched. Try
    # again the next time round -- by then the reference fix is in -- but only
    # once, so a project that simply cannot build does not re-fetch forever.
    if state == declared and (attempts >= 2
                              or all(note.endswith(": ok") for note in notes)):
        return "(already warmed)"
    attempts = attempts + 1 if state == declared else 1
    marker.parent.mkdir(parents=True, exist_ok=True)
    notes = []
    for tool, command in warmups:
        try:
            proc = _sandboxed_command(command, workspace, 600, network=True)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            notes.append(f"{tool}: {exc}")
            continue
        # A baseline is broken on purpose and its restore may legitimately
        # fail. Record it and let the verify command report what that means.
        notes.append(f"{tool}: {'ok' if proc.returncode == 0 else 'failed'}")
    marker.write_text("\n".join([f"{declared} {attempts}", *notes]) + "\n")
    return ", ".join(notes) or "(nothing to warm)"


def run_verify(seed: dict, workspace: Path, timeout: int | None = None
               ) -> tuple[bool | None, str]:
    """Run a seed's verification command; return (passed|None, output).

    ``verify_timeout`` may raise the default 180s for install-heavy seeds, but
    is capped at 360s per the sanctioned spec.
    """
    if timeout is None:
        timeout = min(int(seed.get("verify_timeout") or 180), 360)
    from task_environment import environment_spec, verify_environment
    if environment_spec(seed) is not None:
        return verify_environment(seed, workspace, timeout)
    if not seed.get("verify_cmd"):
        return None, "(no verify_cmd)"
    warm_dependency_cache(workspace)
    try:
        from toolchains import declared_powershell_modules
        proc = _sandboxed_command(verify_argv(seed["verify_cmd"]), workspace,
                                  timeout,
                                  powershell_modules=declared_powershell_modules(seed))
        return proc.returncode == 0, (proc.stdout + "\n" + proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, f"(verify timed out after {timeout}s)"
    except FileNotFoundError as exc:
        return False, f"(verify toolchain missing: {exc})"


def default_signal_dispositions() -> list[str]:
    """A prefix that hands the sandbox signals nobody outside it has decided on.

    A signal set to ``SIG_IGN`` survives every ``fork`` and every ``exec``
    below it, and a shell refuses to install a trap for a signal that was
    already ignored when it started. Launch the pipeline under ``nohup`` and
    ``bash-background-cleanup`` -- whose whole subject is HUP, INT and TERM
    handling -- fails inside the sandbox while passing everywhere else. Whether
    a verifier passes must not depend on how the operator started moonshiner,
    so the dispositions are reset on the way in. Absent GNU coreutils there is
    no prefix and the behaviour is exactly what it was before.
    """
    executable = shutil.which("env")
    if executable is None:
        return []
    supported = subprocess.run([executable, "--default-signal", "true"],
                               capture_output=True)
    return [executable, "--default-signal"] if supported.returncode == 0 else []


def _sandboxed_command(command: list[str], workspace: Path, timeout: int, *,
                       network: bool = False,
                       powershell_modules: list[tuple[str, str]] | None = None):
    """Run seed-controlled commands offline with no home or inherited secrets.

    ``network`` is for the harness's own preparation steps only -- fetching a
    task's declared dependencies -- never for a verify command.
    ``powershell_modules`` are the seed's own declared module versions, which
    decide which of the provisioned versions it is shown.
    """
    if shutil.which("bwrap") is None:
        raise RuntimeError("bubblewrap is required to execute seed commands safely")
    from toolchains import (effective_path, powershell_module_binds,
                            powershell_module_root, powershell_modules_mount,
                            powershell_runtime, powershell_runtime_mount,
                            sandbox_toolchain_root)
    workspace = workspace.resolve()
    # Resolved, because on an ostree host `/srv` is a symlink to `var/srv` and
    # bubblewrap binds through it: the workspace lands at the real directory
    # while the name handed back to the seed stays a symlink. `find "$(dirname
    # -- "$0")"` -- how a shell verifier names its own directory -- then walks
    # the symlink itself and stops, so `javac` was handed an empty source list
    # and every seed built that way failed with "error: no source files".
    sandbox_workspace = Path("/srv").resolve()
    # Everything writable this sandbox needs sits outside the workspace, because
    # the workspace is exactly what a verifier judges. It used to sit inside, as
    # `.sandbox-home`, and the many seeds whose acceptance check is "the working
    # tree holds nothing but my files" failed on the harness's own scratch
    # directory -- a verdict no patch could change. HOME is a dotted name below
    # the sandbox's own temporary directory so it needs no extra mount point and
    # survives a verify command that sweeps `/tmp/*`.
    scratch = verify_scratch(workspace)
    temporary = scratch / "tmp"
    shared_memory = scratch / "shm"
    hidden_home = scratch / "hidden-home"
    sandbox_home = Path("/tmp/.sandbox-home")
    # Mount points for what the harness provides, placed under the hidden home
    # rather than in the workspace: a verifier walks its project directory to
    # judge what the agent left there, and must not find the SDK this harness
    # supplies and read it as the agent having vendored one. The sandbox sees
    # `hidden_home` at the real home path, so a directory made here is the one
    # the toolchain mount paths name.
    toolchain_mounts = hidden_home / sandbox_toolchain_root().relative_to(
        Path.home())
    # Resolved, because `/home` is itself a symlink to `/var/home` on an ostree
    # host: `Path.home()` reports the link and a resolved conda path is not
    # `relative_to` it, which raised before a single sandbox could be built.
    real_home = Path.home().resolve()
    conda = conda_installation()
    hidden_conda = (hidden_home / conda.relative_to(real_home)
                    if conda is not None and conda.is_relative_to(real_home)
                    else None)
    for directory in (temporary, temporary / sandbox_home.name, shared_memory,
                      hidden_home,
                      toolchain_mounts / "powershell",
                      toolchain_mounts / "powershell-modules",
                      *([hidden_conda] if hidden_conda is not None else [])):
        directory.mkdir(parents=True, exist_ok=True)
    accepted_terms = Path.home() / ".conda" / "tos"
    if conda is not None and accepted_terms.is_dir():
        # Conda records which channels' terms have been accepted in the user's
        # home, and this sandbox replaces that home with a throwaway. Without
        # the record conda refuses every channel in `defaults` outright. Copy
        # the decision the user already made rather than making it again on
        # their behalf -- and copy only `tos`, so the analytics token and
        # environment list beside it stay in the home that is being hidden.
        shutil.copytree(accepted_terms,
                        temporary / sandbox_home.name / ".conda" / "tos",
                        dirs_exist_ok=True)
    # PowerCLI asks, on every import into a home that holds no answer, whether
    # you would like to join its telemetry programme -- and asks in nine lines
    # that land in whatever the seed's verifier captured. `vcfarch-0073` reads
    # the JSON its module prints and got the invitation in front of it. This
    # sandbox has no network at all, so there is no participation to decline
    # here beyond the question itself; recording the answer is what stops it
    # being asked.
    settings = (temporary / sandbox_home.name / ".local" / "share" / "VMware"
                / "PowerCLI")
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "PowerCLI_Settings.xml").write_text(
        '<Settings><Setting Name="ParticipateInCEIP" Value="False" /></Settings>')
    sandbox_path = effective_path()
    if conda is not None:
        # Behind the system directories, never in front of them. This PATH is
        # inherited from a shell with conda activated, so conda's `bin` led it
        # -- and the moment conda was genuinely reachable there, `python3`
        # started meaning Anaconda's build, which has no `os.pidfd_open` and
        # broke a seed with nothing to do with conda. `condabin/conda` carries
        # an absolute shebang, so conda still resolves from the back.
        # Resolved before comparing, and the original spelling kept in the
        # result: `is_relative_to` compares text, and PATH says
        # `/home/venom/miniconda3/bin` where the installation resolves to
        # `/var/home/...`. Matched lexically, nothing moved and Anaconda's
        # python went on winning.
        entries = sandbox_path.split(":")
        inside = [entry for entry in entries
                  if Path(entry).resolve().is_relative_to(conda)]
        sandbox_path = ":".join([entry for entry in entries
                                 if entry not in inside] + inside)
    cmd = ["bwrap", "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/"]
    if not network:
        cmd.insert(2, "--unshare-net")
    # Rustup installs its executable shims and toolchains below the user's
    # home. The home itself stays hidden because it may contain credentials;
    # expose only the executable shims and compiler toolchains at neutral,
    # read-only sandbox paths.
    cargo_bin = Path.home() / ".cargo" / "bin"
    rustup_home = Path.home() / ".rustup"
    if cargo_bin.is_dir() and rustup_home.is_dir():
        # /mnt and /media are existing neutral mount points. Binding before the
        # home tmpfs preserves access to the host sources without exposing home.
        cmd += ["--ro-bind", str(cargo_bin), "/mnt",
                "--ro-bind", str(rustup_home), "/media"]
    # The hidden home is writable. A JVM does not read `$HOME`: it resolves
    # `user.home` from the passwd database, so Maven and Gradle look for their
    # caches at the real home path whatever the environment says, and a
    # read-only mount there failed every Maven seed with
    # `LocalRepositoryNotAccessibleException`. Nothing of the real home is
    # here -- it is a fresh directory beside the workspace, removed with it.
    cmd += ["--bind", str(workspace), str(sandbox_workspace),
            "--bind", str(hidden_home), str(Path.home()),
            "--bind", str(temporary), "/tmp",
            "--bind", str(temporary), "/var/tmp"]
    if hidden_conda is not None:
        # After the home mount, not before it: conda's entry points and its own
        # interpreter are shebang-bound to the prefix they were installed at,
        # so it works at that path or nowhere. bubblewrap resolves bind sources
        # against the original root, so the mount that hides the home does not
        # take this one's source away with it.
        cmd += ["--ro-bind", str(conda), str(conda)]
    pwsh = powershell_runtime()
    if pwsh is not None:
        cmd += ["--ro-bind", str(pwsh.parent), powershell_runtime_mount()]
        sandbox_path = powershell_runtime_mount() + ":" + sandbox_path
    module_root = powershell_module_root()
    if module_root.is_dir():
        pinned = powershell_module_binds(powershell_modules or [])
        if pinned:
            for source, destination in pinned:
                cmd += ["--ro-bind", str(source), destination]
        else:
            cmd += ["--ro-bind", str(module_root), powershell_modules_mount()]
    cmd += ["--dev-bind", "/dev", "/dev",
            "--bind", str(shared_memory), "/dev/shm",
            "--proc", "/proc",
            "--clearenv", "--setenv", "PATH", sandbox_path,
            "--setenv", "HOME", str(sandbox_home),
            "--setenv", "TMPDIR", "/tmp",
            "--setenv", "TMP", "/tmp",
            "--setenv", "TEMP", "/tmp",
            "--chdir", str(sandbox_workspace)]
    if module_root.is_dir():
        builtin_modules = powershell_runtime_mount() + "/Modules"
        cmd += ["--setenv", "PSModulePath",
                powershell_modules_mount() + os.pathsep + builtin_modules]
    if cargo_bin.is_dir() and rustup_home.is_dir():
        cmd += [
                "--setenv", "PATH", "/mnt:" + sandbox_path,
                "--setenv", "RUSTUP_HOME", "/media",
                "--setenv", "CARGO_HOME", str(sandbox_home / ".cargo")]
    if conda is not None:
        # The installation is read-only here, so extraction needs a writable
        # directory of its own; the host cache stays second in the list, where
        # conda still reads from it rather than downloading again.
        cmd += ["--setenv", "CONDA_PKGS_DIRS",
                f"{sandbox_home / 'conda-pkgs'},{conda / 'pkgs'}"]
    cmd += ["--", *command]
    cmd = default_signal_dispositions() + cmd
    from runtimes.base import run_with_inactivity_timeout
    return run_with_inactivity_timeout(
        cmd, cwd=workspace, capture_output=True, text=True,
        inactivity_timeout=timeout)


def preflight_seed_environment(seed: dict, runtime=None) -> tuple[bool, str]:
    """Validate the baseline toolchain in the verifier sandbox before model spend.

    ``reference_setup`` belongs to post-patch reference validation. It may
    legitimately invoke a file created by ``reference_fix.patch`` and must
    never run against the intentionally broken baseline used for tracing.
    """
    from task_environment import (environment_spec, prepare_environment,
                                  probe_runtime)
    if environment_spec(seed) is not None:
        provenance = prepare_environment(seed, runtime)
        workspace = materialize(seed, name=f"environment-{seed['id']}")
        environment = (runtime.teacher_environment(workspace)
                       if runtime is not None else {})
        probe_runtime(seed, runtime, workspace, environment)
        _, detail = run_verify(seed, workspace)
        remove_workspace(workspace)
        identity = provenance.get("image_digest") or provenance.get("image_id")
        return True, f"OCI task environment ready ({identity}); baseline: {detail}"

    from toolchains import (declared_commands, declared_powershell_modules,
                            missing_executables, provision,
                            provision_powershell_modules)
    deployed, deployment_detail = provision(declared_commands(seed))
    if not deployed:
        return False, deployment_detail
    modules_deployed, modules_detail = provision_powershell_modules(
        declared_powershell_modules(seed))
    if not modules_deployed:
        return False, modules_detail
    workspace = materialize(seed, name=f"environment-{seed['id']}")
    # Preflight sandboxes are scratch: one per claim, yielding a verdict rather
    # than an artifact. Unremoved they outweigh the traces. A failed one is kept
    # because it is the evidence an infrastructure failure is diagnosed from.
    # remove_workspace refuses anything that is not a workspace, so a caller or
    # test double handing back the wrong path fails loudly instead of deleting.
    scratch: list[Path] = [workspace]

    def finish(ok: bool, detail: str) -> tuple[bool, str]:
        if ok:
            for path in scratch:
                try:
                    remove_workspace(path)
                except ValueError:
                    pass
        return ok, detail

    # The baseline is expected to fail verification; only missing executable
    # evidence is an environment defect. This also discovers nested tools used
    # by shell/Python verifier wrappers without mistaking the intended test
    # failure for an infrastructure failure.
    _, verify_detail = run_verify(seed, workspace)
    missing = missing_executables(verify_detail)
    if missing:
        deployed, deployment_detail = provision(missing)
        if not deployed:
            return finish(False, deployment_detail)
        retry_workspace = materialize(seed, name=f"environment-{seed['id']}-provisioned")
        scratch.append(retry_workspace)
        _, verify_detail = run_verify(seed, retry_workspace)
        still_missing = missing_executables(verify_detail)
        if still_missing:
            return finish(False, "toolchain remains unavailable in verifier sandbox: "
                          + ", ".join(still_missing))
    return finish(True, verify_detail)


def protected_hashes(seed: dict, workspace: Path) -> dict[str, str | None]:
    """Hash protected files so traces that modify tests can be rejected."""
    hashes = {}
    for relative in seed.get("test_files", []):
        path = workspace / relative
        hashes[relative] = (hashlib.sha256(path.read_bytes()).hexdigest()
                            if path.exists() else None)
    from task_environment import environment_control_hashes
    hashes.update(environment_control_hashes(seed))
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


def conda_installation() -> Path | None:
    """Where conda is installed, when it sits below the home the sandbox hides.

    Four seeds build their environment with ``conda env create -p ./env``, and
    conda is installed under the user's home -- the one directory a verifier
    sandbox deliberately masks. ``effective_path()`` went on advertising its
    ``bin``, so PATH named an executable that was no longer there and all four
    failed with ``bwrap: execvp conda: No such file or directory``.
    """
    from toolchains import effective_path
    executable = shutil.which("conda", path=effective_path())
    if executable is None:
        return None
    root = Path(executable).resolve().parent.parent
    return root if (root / "conda-meta").is_dir() else None


def clear_runtime_caches(workspace: Path) -> list[str]:
    """Remove verifier-created caches without touching candidate source.

    A tracked (vendored) ``node_modules`` is preserved; only untracked caches
    and known runtime suffixes are removed.
    """
    tracked_directories: set[str] = set()
    tracked_files: set[str] = set()
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=workspace,
                             capture_output=True)
    if tracked.returncode == 0:
        for value in tracked.stdout.decode(errors="surrogateescape").split("\0"):
            if not value:
                continue
            tracked_files.add(value)
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
            relative = path.relative_to(workspace).as_posix()
            # A seed may ship a compiled file as a fixture. Only what the run
            # itself produced -- what the baseline commit does not have -- is
            # a cache.
            if relative in tracked_files:
                continue
            path.unlink()
            removed.append(relative)
    return sorted(removed)


def scrub_text(value: str, workspace: str | None = None, *,
               strip: bool = True) -> str:
    """Rewrite machine paths to portable placeholders and redact secrets.

    Give a caller `strip=False` when the value is message content rather than
    a captured block of output: trailing whitespace belongs to the trace.
    """
    value = value.replace("\x00", "")
    if workspace:
        value = value.replace(workspace + "/", "").replace(workspace, ".")
    from configuration import PROJECT_ROOT
    if PROJECT_ROOT != ROOT:
        value = value.replace(str(PROJECT_ROOT), "/repo")
    value = value.replace(str(ROOT), "/repo")
    home = Path.home()
    resolved_home = home.resolve()
    if resolved_home != home:
        value = value.replace(str(resolved_home), "/home/user")
    value = value.replace(str(home), "/home/user")
    value = RUNTIME_PATH_RE.sub("/runtime", value)
    static_names = ("ZAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "OPENROUTER_API_KEY", "HF_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
    for env_name in dict.fromkeys(static_names + provider_key_env_names()):
        secret = os.environ.get(env_name)
        if secret:
            value = value.replace(secret, "[REDACTED_SECRET]")
    for secret in _staged_secret_values():
        value = value.replace(secret, "[REDACTED_SECRET]")
    from privacy import redact
    scrubbed = redact(value, exact_secrets=_staged_secret_values())[0]
    return scrubbed.strip() if strip else scrubbed
