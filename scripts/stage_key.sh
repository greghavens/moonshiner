#!/usr/bin/env bash
# Stage a provider API key where the harness reads it (src/runtimes/auth.py):
# a mode-0600 file under $XDG_RUNTIME_DIR — RAM-backed, per-user, readable by
# the detached batch scope. Keys are PER PROVIDER: the file name derives from
# the runtime's provider in config.json (moonshiner-<provider>-key) unless
# key_file_name overrides it, so several keyed providers can coexist in a run.
#
#   scripts/stage_key.sh               # teacher's runtime, silent prompt
#   scripts/stage_key.sh pi            # a runtime by name
#   scripts/stage_key.sh pi < key.txt  # non-interactive: key on stdin
#
# The key is read from stdin (silent prompt on a TTY) so it never appears in
# argv, shell history, or logs. Never commit a key or write one in the repo.
set -euo pipefail
cd "$(dirname "$0")/.."

runtime_name="${1:-}"
mapfile -t resolved < <(python3 - "$runtime_name" <<'PY'
import sys

sys.path.insert(0, "src")
from common import CONFIG, key_env_name, key_file_path  # single source of truth

name = sys.argv[1] or CONFIG["teacher"]["runtime"]
runtimes = CONFIG.get("runtimes") or {}
if name not in runtimes:
    raise SystemExit(f"unknown runtime {name!r}; configured: "
                     f"{', '.join(sorted(runtimes))}")
try:
    env_name = key_env_name(runtimes[name])
    file_path = key_file_path(runtimes[name])
except RuntimeError as error:
    raise SystemExit(f"runtime {name!r} is not a keyed provider: {error}")
print(name)
print(env_name)
print(file_path)
PY
)
if [ "${#resolved[@]}" -ne 3 ]; then
  echo "[stage-key] could not resolve runtime credential config" >&2
  exit 1
fi
runtime="${resolved[0]}"; env_name="${resolved[1]}"; key_path="${resolved[2]}"

if [ -t 0 ]; then
  read -rs -p "[stage-key] paste the $env_name value for runtime '$runtime': " key
  echo
else
  key="$(cat)"
fi
key="$(printf '%s' "$key" | tr -d '[:space:]')"
if [ -z "$key" ]; then
  echo "[stage-key] no key provided" >&2
  exit 1
fi

umask 077
printf '%s' "$key" > "$key_path"
chmod 600 "$key_path"
echo "[stage-key] staged: $key_path (mode 0600, tmpfs — cleared on reboot)"
echo "[stage-key] runtime '$runtime' reads \$$env_name first, then this file"
