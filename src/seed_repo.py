"""The Moonshiner repository clone that authored seeds are written into.

Moonshiner is the source of Moonshiner seeds. When seed authoring is enabled a
checkout of the product repository must exist on disk, because an accepted seed
is promoted straight into its ``tasks/seeds`` and committed from there. There is
no external seed source and no staging copy: a seed that exists is a seed in the
repository.

Only judge-accepted seeds ever reach the clone. ``seed_pipeline`` keeps rejected
candidates in project state and writes ``SEEDS_DIR`` on one line, after the
acceptance gate.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

DEFAULT_REMOTE = "https://github.com/greghavens/moonshiner.git"
DEFAULT_PATH = Path.home() / "moonshiner"
BRANCH = "main"


def configured_path(config: dict | None = None) -> Path:
    """Resolve where the seed repository clone lives."""
    if config is None:
        from common import CONFIG
        config = CONFIG
    value = str(((config.get("seeds") or {}).get("repo_path") or "")).strip()
    return (Path(value).expanduser() if value else DEFAULT_PATH).resolve()


def configured_remote(config: dict | None = None) -> str:
    if config is None:
        from common import CONFIG
        config = CONFIG
    return (str(((config.get("seeds") or {}).get("repo_remote") or "")).strip()
            or DEFAULT_REMOTE)


def seeds_dir(config: dict | None = None) -> Path:
    return configured_path(config) / "tasks" / "seeds"


def current_branch(path: Path) -> str:
    result = subprocess.run(["git", "symbolic-ref", "--short", "-q", "HEAD"],
                            cwd=path, capture_output=True, text=True)
    return result.stdout.strip() or "detached"


def ensure(path: Path | None = None, *, remote: str | None = None) -> Path:
    """Return the clone, creating it if absent. Never touches an unrelated tree.

    Authored seeds are untracked until committed, so a dirty tree is the normal
    state here and is not an error. Being off ``main`` is: the sync commits and
    pushes there, and committing seeds onto another branch would strand them.
    """
    path = path or configured_path()
    remote = remote or configured_remote()
    if not (path / ".git").is_dir():
        if path.exists() and any(path.iterdir()):
            raise SystemExit(
                f"{path} exists but is not a git checkout; set seeds.repo_path "
                "to the Moonshiner clone that seeds should be committed into")
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Cloning the Moonshiner seed repository into {path}…")
        subprocess.run(["git", "clone", "--branch", BRANCH, remote, str(path)],
                       check=True)
    if not (path / "tasks" / "seeds").is_dir():
        raise SystemExit(f"{path} is not a Moonshiner checkout (no tasks/seeds)")
    branch = current_branch(path)
    if branch != BRANCH:
        raise SystemExit(
            f"seed repository {path} is on {branch!r}, not {BRANCH!r}; "
            "authored seeds are committed to main")
    return path
