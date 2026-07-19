"""Layered Moonshiner configuration and safe dotted-key updates."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("MOONSHINER_BUNDLE_ROOT",
                           Path(__file__).resolve().parent.parent)).resolve()
DEFAULT_PATH = ROOT / "config.json"
PROJECT_ROOT = Path.cwd().resolve()
PROJECT_STATE = PROJECT_ROOT / ".moonshiner"
LOCAL_PATH = PROJECT_STATE / "config.json"


def user_config_path() -> Path:
    """Legacy machine-wide config path (credentials do not use this file)."""
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "moonshiner" / "config.json"


def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config() -> dict:
    config = json.loads(DEFAULT_PATH.read_text())
    if LOCAL_PATH.exists():
        config = deep_merge(config, json.loads(LOCAL_PATH.read_text()))
    return config


def project_confirmed() -> bool:
    """Return whether this exact working directory approved local state."""
    if not LOCAL_PATH.is_file():
        return False
    try:
        local = json.loads(LOCAL_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return (local.get("workspace") or {}).get("confirmed_root") == str(PROJECT_ROOT)


def confirm_project(*, input_fn=input, output_fn=print) -> bool:
    """Ask before creating this directory's independent config/output tree."""
    if project_confirmed():
        return True
    output_fn("Moonshiner uses the current directory as an independent project:")
    output_fn(f"  project: {PROJECT_ROOT}")
    output_fn(f"  config and output: {PROJECT_STATE}")
    try:
        answer = input_fn("Create and use this project here? [Y/n]: ").strip().lower()
    except EOFError:
        output_fn("Confirmation requires an interactive terminal; no files were created.")
        return False
    if answer not in {"", "y", "yes"}:
        output_fn("No files were created or changed.")
        return False

    local: dict = {}
    if LOCAL_PATH.is_file():
        local = json.loads(LOCAL_PATH.read_text())
    else:
        # Preserve a checkout's pre-project configuration on first migration.
        legacy = ROOT / "config.local.json"
        if PROJECT_ROOT == ROOT and legacy.is_file():
            local = json.loads(legacy.read_text())
    dotted_set(local, "workspace.confirmed_root", str(PROJECT_ROOT))
    dotted_set(local, "storage.root", str(PROJECT_STATE))
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOCAL_PATH.write_text(json.dumps(local, indent=2) + "\n")
    LOCAL_PATH.chmod(0o600)
    output_fn(f"Using {PROJECT_STATE}")
    return True


def parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def dotted_get(config: dict, dotted: str) -> Any:
    value: Any = config
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(dotted)
        value = value[part]
    return value


def dotted_set(config: dict, dotted: str, value: Any) -> None:
    parts = [part for part in dotted.split(".") if part]
    if not parts:
        raise ValueError("configuration key cannot be empty")
    node = config
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"{part!r} is not a configuration object")
        node = child
    node[parts[-1]] = value


def update_local(dotted: str, value: Any) -> Path:
    overrides = json.loads(LOCAL_PATH.read_text()) if LOCAL_PATH.exists() else {}
    dotted_set(overrides, dotted, value)
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOCAL_PATH.write_text(json.dumps(overrides, indent=2) + "\n")
    LOCAL_PATH.chmod(0o600)
    return LOCAL_PATH
