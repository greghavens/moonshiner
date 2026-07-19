"""Layered Moonshiner configuration and safe dotted-key updates."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config.json"
LOCAL_PATH = ROOT / "config.local.json"


def user_config_path() -> Path:
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
    # User preferences are machine-wide; repo-local choices win for a checkout.
    for path in (user_config_path(), LOCAL_PATH):
        if path.exists():
            config = deep_merge(config, json.loads(path.read_text()))
    return config


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
    LOCAL_PATH.write_text(json.dumps(overrides, indent=2) + "\n")
    return LOCAL_PATH
