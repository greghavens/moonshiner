"""Installed console entry point."""
from __future__ import annotations
import json, os, shutil, sys, uuid
from pathlib import Path

def main() -> int:
    bundle = Path(__file__).resolve().parent / "bundle"
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home()/".config"))/"moonshiner"/"config.json"
    if "MOONSHINER_HOME" not in os.environ and config.exists():
        try:
            root=(json.loads(config.read_text()).get("storage") or {}).get("root")
            if root: os.environ["MOONSHINER_HOME"] = str(Path(root).expanduser())
        except (OSError, json.JSONDecodeError): pass
    os.environ.setdefault("MOONSHINER_BUNDLE_ROOT", str(bundle))
    storage = Path(os.environ.get("MOONSHINER_HOME") or
                   Path(os.environ.get("XDG_DATA_HOME", Path.home()/".local/share"))/"moonshiner")
    active = storage.expanduser() / "corpora" / "active"
    if not (active / "tasks" / "seeds").is_dir():
        active.parent.mkdir(parents=True, exist_ok=True)
        staging = active.with_name(f".active-staging-{uuid.uuid4().hex}")
        (staging / "tasks").mkdir(parents=True)
        shutil.copytree(bundle / "tasks" / "seeds", staging / "tasks" / "seeds")
        for name in ("corpus-version.json", "SEED_CATALOG.md", "SEED_CATALOG.json"):
            if (bundle / name).is_file(): shutil.copy2(bundle / name, staging / name)
        try: staging.replace(active)
        except FileExistsError: shutil.rmtree(staging, ignore_errors=True)
    sys.path.insert(0, str(bundle / "src")); sys.path.insert(0, str(bundle))
    from moonshiner import main as application_main
    return int(application_main())
