"""Provider-credential loading for metered runtimes.

Keys are **per provider**: each keyed runtime resolves its own environment
variable (``key_env``, default ``<PROVIDER>_API_KEY`` derived from its
``provider``) and its own staged file (``key_file_name``, default
``moonshiner-<provider>-key`` under ``$XDG_RUNTIME_DIR``), so several keyed
providers can be live in one run without colliding.
``scripts/stage_key.sh <runtime>`` writes the staged file.

The real key never enters a teacher sandbox. It is read host-side and handed
only to the loopback proxy; the child agent sees a dummy token.
"""
from __future__ import annotations

import os

from common import key_env_name, key_file_path

__all__ = ["key_env_name", "key_file_path", "load_provider_key"]


def load_provider_key(runtime_config: dict | None) -> str:
    """Return this runtime's provider key, or raise if neither source has it.

    Order: the runtime's ``key_env`` environment variable, then its staged
    runtime file. A missing or keyless runtime config raises before any
    network call — a credential can never silently come from another
    provider's source.
    """
    if runtime_config is None:
        raise RuntimeError("provider credential missing: no runtime config")
    env_name = key_env_name(runtime_config)   # raises on a keyless config
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    path = key_file_path(runtime_config)
    try:
        staged = path.read_text().strip()
    except FileNotFoundError:
        staged = ""
    if staged:
        return staged
    raise RuntimeError(
        f"provider credential missing: set ${env_name} or stage {path} "
        f"(scripts/stage_key.sh)")
