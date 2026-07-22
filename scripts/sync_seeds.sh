#!/usr/bin/env bash
# Unattended configured-source -> Moonshiner seed sync.
#
# Runs from the seed-sync.timer systemd user unit (every 5 minutes, survives
# reboots via loginctl linger); also safe to run by hand. Canonical unit
# files live next to this script; install with:
#
#   cp scripts/seed-sync.{service,timer} ~/.config/systemd/user/
#   systemctl --user daemon-reload
#   systemctl --user enable --now seed-sync.timer
#   loginctl enable-linger "$USER"
#
# Per run:
#   - A seed present in the configured source but not here is copied only after
#     two consecutive runs observe an identical content snapshot, so a seed
#     the authoring pipeline is still writing is never taken half-done.
#   - Copies are gated behind scripts/check.sh. Green: committed with the
#     established "Bring in N new seeds" message and pushed. Red: nothing is
#     committed, the unit fails visibly, and the next tick re-copies fresh.
#   - A failed push (offline; keyring still locked right after boot) leaves
#     the commit local and is retried on every subsequent run.
#   - The configured source is strictly read-only.
set -euo pipefail

SRC=${MOONSHINER_SEED_SOURCE:-}
ACCEPTED=${MOONSHINER_SEED_ACCEPTED_DIR:-}
REPO=$(cd "$(dirname "$0")/.." && pwd)
DST=$REPO/tasks/seeds
STATE=$REPO/.git/seed-sync-snapshots

exec 9>"$REPO/.git/seed-sync.lock"
flock -n 9 || { echo "another sync holds the lock; skipping"; exit 0; }

cd "$REPO"
[ -n "$SRC" ] || { echo "MOONSHINER_SEED_SOURCE is not configured; skipping"; exit 0; }
[ -d "$SRC" ] || { echo "source $SRC missing; skipping"; exit 0; }
[ -n "$ACCEPTED" ] || { echo "MOONSHINER_SEED_ACCEPTED_DIR is not configured; refusing unreviewed sync" >&2; exit 1; }
[ -d "$ACCEPTED" ] || { echo "accepted-marker directory $ACCEPTED missing; refusing unreviewed sync" >&2; exit 1; }

branch=$(git symbolic-ref --short -q HEAD || echo detached)
if [ "$branch" != main ]; then
  echo "ERROR: repo is on '$branch', not main; refusing to sync" >&2
  exit 1
fi

snapshot() { find "$SRC/$1" -type f -printf '%P %s\n' 2>/dev/null | sort | sha256sum | cut -d' ' -f1; }

# The upstream author writes one immutable chunk marker only after deterministic
# validation and independent semantic acceptance.  Directory stability alone
# is not approval: incomplete and rejected seeds remain visible upstream.
mapfile -t accepted_ids < <(
  for marker in "$ACCEPTED"/*.json; do
    [ -f "$marker" ] || continue
    jq -er '.seed_ids[] | strings' "$marker"
  done | LC_ALL=C sort -u
)
declare -A accepted=()
for name in "${accepted_ids[@]}"; do accepted[$name]=1; done
mapfile -t absent < <(comm -13 <(ls "$DST" | sort) \
  <(comm -12 <(printf '%s\n' "${accepted_ids[@]}") <(ls "$SRC" | sort)))
# Copied by an earlier run whose check.sh failed: still untracked, so refresh
# from source and try again rather than leaving a stale half-import behind.
mapfile -t uncommitted < <(git ls-files --others --exclude-standard --directory tasks/seeds/ | cut -d/ -f2 | sort -u)

declare -A prev=()
[ -f "$STATE" ] && while read -r n h; do [ -n "${h:-}" ] && prev[$n]=$h; done <"$STATE"

copy=()
: >"$STATE.next"
for name in "${absent[@]}"; do
  [ -n "$name" ] && [ -d "$SRC/$name" ] || continue
  h=$(snapshot "$name")
  echo "$name $h" >>"$STATE.next"
  if [ "${prev[$name]:-}" = "$h" ]; then
    copy+=("$name")
  else
    echo "holding $name until its snapshot is stable across two runs"
  fi
done
mv "$STATE.next" "$STATE"

for name in "${uncommitted[@]}"; do
  [ -n "$name" ] && [ -n "${accepted[$name]:-}" ] && \
    [ -d "$SRC/$name" ] && copy+=("$name")
done

if [ "${#copy[@]}" -gt 0 ]; then
  before=$(ls "$DST" | wc -l)
  new=0
  for name in "${copy[@]}"; do
    [ -d "$DST/$name" ] || new=$((new + 1))
    if [ -e "$DST/$name" ]; then
      echo "refusing to replace existing seed $name" >&2
      continue
    fi
    cp -a -- "$SRC/$name" "$DST/$name"
    echo "copied $name"
  done
  after=$((before + new))

  if scripts/check.sh; then
    names=$(printf '%s, ' "${copy[@]}")
    names=${names%, }
    plural=""
    [ "${#copy[@]}" -ne 1 ] && plural="s"
    paths=()
    for name in "${copy[@]}"; do paths+=("tasks/seeds/$name"); done
    git add -- "${paths[@]}"
    git commit \
      -m "Bring in ${#copy[@]} new seed${plural} from configured source: $names" \
      -m "Reviewed copy from the configured seed source via the seed-sync service. Corpus $before -> $after; check.sh green." \
      -- "${paths[@]}"
    echo "committed ${#copy[@]} seed(s): $names"
  else
    echo "ERROR: check.sh failed; ${copy[*]} left uncommitted, retrying next run" >&2
    exit 1
  fi
fi

# Push anything unpushed — this run's commit or an earlier one that could not
# reach GitHub at the time.
if [ -n "$(git rev-list origin/main..HEAD 2>/dev/null)" ]; then
  if git push origin main; then
    echo "pushed"
  else
    echo "WARNING: push failed; commit is safe locally, retrying next run" >&2
  fi
fi
echo "seed-sync: done"
