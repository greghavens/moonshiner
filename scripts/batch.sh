#!/usr/bin/env bash
# Run a long job detached in a systemd --user scope so a Claude Code session
# teardown cannot kill it (this box has linger=yes; a session cgroup teardown
# would otherwise take the job with it). Output is logged to runs/<name>-<ts>/.
#
#   scripts/batch.sh <name> <command...>
#
#   scripts/batch.sh full   python3 moonshiner.py run          # whole pipeline
#   scripts/batch.sh traces python3 moonshiner.py generate --all
#   scripts/batch.sh screen python3 moonshiner.py screen --all --review
#   scripts/batch.sh review bash scripts/quality_loop.sh        # rolling loop
#
# Follow:  tail -f runs/<name>-<ts>/run.log
# Stop:    systemctl --user stop <unit>   (printed on launch)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -lt 2 ]; then
  echo "usage: scripts/batch.sh <name> <command...>" >&2
  exit 2
fi

name="$1"; shift
stamp=$(date +%Y%m%d-%H%M%S)          # a shell timestamp, not model state
logdir="runs/${name}-${stamp}"
unit="moonshiner-${name}-${stamp}"
mkdir -p "$logdir"
log="$logdir/run.log"

echo "[batch] unit: $unit"
echo "[batch] log:  $log"
{
  echo "# moonshiner batch: $name"
  echo "# command: $*"
  echo "# started: $stamp"
} >"$log"

# A service (not a client-owned transient scope) remains supervised after the
# launching shell exits. Preserve PATH so configured harness CLIs remain usable.
systemd-run --user --collect --unit="$unit" --working-directory="$PWD" \
  --property=Restart=on-failure --property=RestartSec=10s \
  --property=StandardOutput="append:$PWD/$log" \
  --property=StandardError="append:$PWD/$log" \
  --setenv=PATH="$PATH" --setenv=MOONSHINER_SUPERVISED=1 "$@" >/dev/null

echo "[batch] detached. follow: tail -f $log"
echo "[batch] stop:     systemctl --user stop $unit"
