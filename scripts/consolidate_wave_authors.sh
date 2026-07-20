#!/usr/bin/env bash
# Preserve both paid child cycles, retire their frozen schedulers, then launch
# the single unified Wave 10+ authoring lane.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
explicit=moonshiner-explicit-waves-20260719-173514.service
matrix=moonshiner-matrix-waves-20260719-173608.service

declare -A child=()
for unit in "$explicit" "$matrix"; do
  main=$(systemctl --user show "$unit" -p MainPID --value 2>/dev/null || true)
  [ -n "$main" ] || continue
  [ "$main" -gt 0 ] || continue
  child[$unit]=$(pgrep -P "$main" | head -1 || true)
  kill -STOP "$main"
  echo "froze scheduler $unit; preserving child ${child[$unit]:-none}"
done

for unit in "$explicit" "$matrix"; do
  pid=${child[$unit]:-}
  while [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; do sleep 2; done
  systemctl --user stop "$unit" 2>/dev/null || true
  echo "retired $unit after its paid child completed"
done

exec "$root/scripts/batch.sh" all-waves \
  /home/linuxbrew/.linuxbrew/bin/python3 "$root/src/author_all_waves.py" \
  --catalog-dir "$root/.moonshiner/imports/sol-code/tasks" --yes
