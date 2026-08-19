"""Installed console entry point."""
from __future__ import annotations
import os, shutil, stat, sys, uuid
from pathlib import Path

from . import __version__


def _is_read_only(argv: list[str]) -> bool:
    return bool(argv and (
        argv[0] in {"-h", "--help", "help", "--version"}
        or argv in (["dataset", "build", "--help"],
                    ["dataset", "build", "-h"])
        or (argv[0] == "seeds" and len(argv) > 1
            and argv[1] in {"status", "verify", "list", "catalog", "manifest"})
    ))


def _replace_file(source: str, destination: str) -> None:
    """Copy over a seed fixture that may itself be read-only.

    Seeds ship protected verifiers, keys and fixture databases with the write
    bit cleared, so a plain copy onto an installed corpus fails with
    PermissionError and takes the whole update down with it.
    """
    try:
        os.chmod(destination, os.stat(destination).st_mode | stat.S_IWUSR)
    except OSError:
        pass
    shutil.copy2(source, destination)


def _read_stamp(path: Path) -> str:
    """The release whose seeds this corpus was last populated from."""
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _stamp(path: Path, release: str) -> None:
    try:
        path.write_text(release + "\n")
    except OSError:
        pass


# Installing the package byte-compiles the corpus it carries. That bytecode is
# not seed content and must not be copied into a project's corpus, where it
# would be materialized into workspaces and fingerprinted as part of a seed.
_NOT_SEED_CONTENT = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def _force_writable(function, path, _exception) -> None:
    """Let removal past a seed's read-only fixture, then try once more."""
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    except OSError:
        return
    function(path)


def _merge_seeds(source: Path, destination: Path) -> None:
    """Bring every released seed up to date without disturbing local ones.

    Each released seed is replaced whole rather than copied over in place. A
    release may move a seed's files -- the VCF seeds whose payload moved under
    `files/` did -- and copying onto what is already there leaves the old paths
    beside the new ones, so the seed materializes with two copies of itself and
    a verifier reads the stale one. Seeds the release does not contain are
    authored here and not released yet; they are left exactly as they are.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for seed in sorted(source.iterdir()):
        if not seed.is_dir():
            continue
        target = destination / seed.name
        if target.exists():
            shutil.rmtree(target, onexc=_force_writable)
        shutil.copytree(seed, target, copy_function=_replace_file,
                        ignore=_NOT_SEED_CONTENT)


def install_corpus(bundle: Path, active: Path, release: str = __version__) -> None:
    """Put this release's seed corpus in place for the project to use.

    A project that authors seeds keeps working from its active corpus, so a
    seed corrected in a release would otherwise never reach it. The active
    corpus also holds seeds authored here and not released yet, so an update
    merges the release in rather than replacing what is there.
    """
    stamp = active / ".installed-release"
    if not (active / "tasks" / "seeds").is_dir():
        active.parent.mkdir(parents=True, exist_ok=True)
        staging = active.with_name(f".active-staging-{uuid.uuid4().hex}")
        (staging / "tasks").mkdir(parents=True)
        shutil.copytree(bundle / "tasks" / "seeds", staging / "tasks" / "seeds",
                        ignore=_NOT_SEED_CONTENT)
        if (bundle / "tasks" / "behavior-worlds.json").is_file():
            shutil.copy2(bundle / "tasks" / "behavior-worlds.json",
                         staging / "tasks" / "behavior-worlds.json")
        for name in ("corpus-version.json", "SEED_CATALOG.md", "SEED_CATALOG.json"):
            if (bundle / name).is_file(): shutil.copy2(bundle / name, staging / name)
        try: staging.replace(active)
        except FileExistsError: shutil.rmtree(staging, ignore_errors=True)
    elif _read_stamp(stamp) == release:
        return
    else:
        _merge_seeds(bundle / "tasks" / "seeds", active / "tasks" / "seeds")
        for name in ("corpus-version.json", "SEED_CATALOG.md", "SEED_CATALOG.json"):
            if (bundle / name).is_file(): shutil.copy2(bundle / name, active / name)
        worlds = bundle / "tasks" / "behavior-worlds.json"
        if worlds.is_file(): shutil.copy2(worlds, active / "tasks" / worlds.name)
    _stamp(stamp, release)


def _run_application(application_main) -> int:
    try:
        return int(application_main())
    except KeyboardInterrupt:
        print("Exiting.")
        return 130

def main() -> int:
    bundle = Path(__file__).resolve().parent / "bundle"
    os.environ.setdefault("MOONSHINER_BUNDLE_ROOT", str(bundle))
    sys.path.insert(0, str(bundle / "src")); sys.path.insert(0, str(bundle))
    from configuration import PROJECT_STATE, confirm_project
    read_only = _is_read_only(sys.argv[1:])
    if not read_only and not confirm_project():
        return 1
    storage = PROJECT_STATE
    os.environ["MOONSHINER_HOME"] = str(storage)
    if read_only:
        from moonshiner import main as application_main
        return _run_application(application_main)
    active = storage.expanduser() / "corpora" / "active"
    install_corpus(bundle, active)
    (active / "tasks").mkdir(parents=True, exist_ok=True)
    if not (active / "tasks" / "behavior-worlds.json").is_file() and \
            (bundle / "tasks" / "behavior-worlds.json").is_file():
        shutil.copy2(bundle / "tasks" / "behavior-worlds.json",
                     active / "tasks" / "behavior-worlds.json")
    from moonshiner import main as application_main
    return _run_application(application_main)
