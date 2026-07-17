#!/usr/bin/env bash
# Rolling quality loop: screen first-pass traces with the configured judge, then
# repair standing rejections, a few at a time, until both drain. Metered — it
# drives the judge (and, for repair, the teacher). It defers cleanly when a
# runtime hits a usage-limit backoff (the phase exits non-zero; the loop waits
# and retries). Run detached with:  scripts/batch.sh review bash scripts/quality_loop.sh
#
#   scripts/quality_loop.sh [batch_size] [idle_passes] [sleep_seconds]
#     batch_size    traces handled per screen/repair step (default 4)
#     idle_passes   consecutive drained passes before stopping   (default 2)
#     sleep_seconds pause between passes                          (default 5)
set -uo pipefail
cd "$(dirname "$0")/.."

batch="${1:-4}"
max_idle="${2:-2}"
nap="${3:-5}"
idle=0
pass=0

trap 'echo "[quality] interrupted"; exit 130' INT TERM

while [ "$idle" -lt "$max_idle" ]; do
  pass=$((pass + 1))
  worked=0
  echo "== quality pass $pass (idle $idle/$max_idle) =="

  # 1) Screen pending first-pass traces; leave rejections for the repair step.
  screen_out=$(python3 moonshiner.py screen --all --review --pending-only \
                 --skip-rejections --limit "$batch" 2>&1) || true
  echo "$screen_out"
  echo "$screen_out" | grep -q "no seeds to screen" || worked=1

  # 2) Repair standing rejections (retrace + rescreen), oldest first.
  retry_out=$(python3 moonshiner.py retry --limit "$batch" 2>&1) || true
  echo "$retry_out"
  echo "$retry_out" | grep -q "no current rejected traces" || worked=1

  if [ "$worked" -eq 0 ]; then
    idle=$((idle + 1))
  else
    idle=0
  fi
  sleep "$nap"
done

echo "[quality] drained after $pass passes"
