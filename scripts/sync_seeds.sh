#!/usr/bin/env bash
# Commit and push seeds that authoring has promoted into the Moonshiner clone.
#
# Moonshiner is the source of Moonshiner seeds. Seed authoring writes an
# accepted seed straight into this repository's tasks/seeds, so this script
# never copies or imports anything: it publishes what is already on disk.
#
# Runs from the seed-sync.timer systemd user unit; also safe to run by hand.
# Install both units from the release with:
#
#   moonshiner seed-sync install
#
# Per run:
#   - Untracked seed directories under tasks/seeds are the candidates. Each one
#     is already judge-accepted; seed_pipeline writes tasks/seeds only after the
#     acceptance gate and keeps rejected candidates in project state.
#   - Copies are gated behind scripts/check.sh. Green: committed with the
#     established "Bring in N new seeds" message and pushed. Red: nothing is
#     committed and the unit fails visibly, leaving the seeds in place.
#   - A failed push (offline; keyring still locked right after boot) leaves the
#     commit local and is retried on every subsequent run.
set -euo pipefail

REPO=${MOONSHINER_SEED_REPO_PATH:-}
[ -n "$REPO" ] || { echo "MOONSHINER_SEED_REPO_PATH is not configured" >&2; exit 1; }
[ -d "$REPO/.git" ] || { echo "$REPO is not a git checkout" >&2; exit 1; }
DST=$REPO/tasks/seeds
[ -d "$DST" ] || { echo "$REPO is not a Moonshiner checkout (no tasks/seeds)" >&2; exit 1; }

exec 9>"$REPO/.git/seed-sync.lock"
flock -n 9 || { echo "another sync holds the lock; skipping"; exit 0; }

cd "$REPO"

branch=$(git symbolic-ref --short -q HEAD || echo detached)
if [ "$branch" != main ]; then
  echo "ERROR: repo is on '$branch', not main; refusing to sync" >&2
  exit 1
fi

# Authoring promotes a whole seed directory at once, so a wholly untracked
# directory directly under tasks/seeds is a finished seed awaiting publication.
# Anchor to exactly one path component: --directory also collapses untracked
# subdirectories inside seeds that are already tracked (build output, agent
# scratch), and those are not new seeds.
mapfile -t new < <(git ls-files --others --exclude-standard --directory tasks/seeds/ \
  | grep -E '^tasks/seeds/[^/]+/$' \
  | sed 's#^tasks/seeds/##; s#/$##' | LC_ALL=C sort -u)

if [ "${#new[@]}" -gt 0 ]; then
  before=$(( $(ls "$DST" | wc -l) - ${#new[@]} ))
  after=$(ls "$DST" | wc -l)

  if scripts/check.sh; then
    names=$(printf '%s, ' "${new[@]}")
    names=${names%, }
    plural=""
    [ "${#new[@]}" -ne 1 ] && plural="s"
    paths=()
    for name in "${new[@]}"; do paths+=("tasks/seeds/$name"); done
    # The catalog is regenerated beside the seeds at promotion time; carry it
    # with the seeds it describes rather than leaving the repo inconsistent.
    for catalog in SEED_CATALOG.md SEED_CATALOG.json; do
      [ -f "$catalog" ] && paths+=("$catalog")
    done
    git add -- "${paths[@]}"
    git commit \
      -m "Bring in ${#new[@]} new seed${plural}: $names" \
      -m "Authored and judge-accepted by the Moonshiner seed pipeline. Corpus $before -> $after; check.sh green." \
      -- "${paths[@]}"
    echo "committed ${#new[@]} seed(s): $names"
  else
    echo "ERROR: check.sh failed; ${new[*]} left uncommitted, retrying next run" >&2
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
