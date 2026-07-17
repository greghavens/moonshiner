"""Provider-credential loading for metered runtimes (currently Pi/Z.ai).

The real key never enters a teacher sandbox. It is read host-side from either
the configured environment variable or a mode-0600 file staged under
``$XDG_RUNTIME_DIR`` and handed only to the loopback proxy; the child agent
sees a dummy token. ``scripts/stage_key.sh`` writes the file.
"""
from __future__ import annotations

import os
from pathlib import Path


def _runtime_dir() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg)
    return Path(f"/run/user/{os.getuid()}")


def key_file_path(runtime_config: dict) -> Path:
    name = runtime_config.get("key_file_name", "moonshiner-provider-key")
    return _runtime_dir() / name


def load_provider_key(runtime_config: dict | None) -> str:
    """Return the provider API key, or raise if neither source is present.

    Order: the configured environment variable, then the staged runtime file.
    An explicitly empty ``runtime_config`` disables the file fallback so tests
    can prove a missing key fails before any network call.
    """
    if runtime_config is None:
        raise RuntimeError("provider credential missing: no runtime config")
    env_name = runtime_config.get("key_env", "ZAI_API_KEY")
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    if runtime_config:
        path = key_file_path(runtime_config)
        try:
            staged = path.read_text().strip()
        except FileNotFoundError:
            staged = ""
        if staged:
            return staged
    raise RuntimeError(
        f"provider credential missing: set ${env_name} or stage "
        f"{key_file_path(runtime_config) if runtime_config else '(no file)'}")
