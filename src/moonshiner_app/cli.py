"""Installed console entry point."""
from __future__ import annotations
import os, shutil, sys, uuid
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
        shutil.copytree(bundle / "tasks" / "seeds", staging / "tasks" / "seeds")
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
        shutil.copytree(bundle / "tasks" / "seeds", active / "tasks" / "seeds",
                        dirs_exist_ok=True)
        for name in ("corpus-version.json", "SEED_CATALOG.md", "SEED_CATALOG.json"):
            if (bundle / name).is_file(): shutil.copy2(bundle / name, active / name)
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
