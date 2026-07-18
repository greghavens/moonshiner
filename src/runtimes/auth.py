"""Provider-credential loading for metered runtimes.

Keys are **per provider**: each keyed runtime resolves its own environment
variable (``key_env``, default ``<PROVIDER>_API_KEY`` derived from its
``provider``) and its own key files (``key_file_name``, default
``moonshiner-<provider>-key``): a staged copy under ``$XDG_RUNTIME_DIR``
(tmpfs, cleared on reboot) and a persistent copy under
``$XDG_CONFIG_HOME/moonshiner`` that survives reboots, so several keyed
providers can be live in one run without colliding.
``scripts/stage_key.sh <runtime>`` writes both files.

The real key never enters a teacher sandbox. It is read host-side and handed
only to the loopback proxy; the child agent sees a dummy token.
"""
from __future__ import annotations

import os

from common import key_env_name, key_file_path, key_persist_path

__all__ = ["key_env_name", "key_file_path", "key_persist_path",
           "load_provider_key"]


def load_provider_key(runtime_config: dict | None) -> str:
    """Return this runtime's provider key, or raise if no source has it.

    Order: the runtime's ``key_env`` environment variable, then its staged
    tmpfs file, then its persistent config file — so a reboot (which clears
    the tmpfs copy) does not lose the credential. A missing or keyless
    runtime config raises before any network call — a credential can never
    silently come from another provider's source.
    """
    if runtime_config is None:
        raise RuntimeError("provider credential missing: no runtime config")
    env_name = key_env_name(runtime_config)   # raises on a keyless config
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    for path in (key_file_path(runtime_config), key_persist_path(runtime_config)):
        try:
            stored = path.read_text().strip()
        except FileNotFoundError:
            continue
        if stored:
            return stored
    raise RuntimeError(
        f"provider credential missing: set ${env_name} or run "
        f"scripts/stage_key.sh (writes {key_file_path(runtime_config)} "
        f"and {key_persist_path(runtime_config)})")
