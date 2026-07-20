#!/usr/bin/env bash
# Maintain a read-only, persistent sparse checkout of the accepted seed source.
set -euo pipefail

repo=${MOONSHINER_SEED_REPOSITORY:-https://github.com/greghavens/sol-code.git}
checkout=${MOONSHINER_SEED_CHECKOUT:-"$(cd "$(dirname "$0")/.." && pwd)/.moonshiner/imports/sol-code-seeds"}
mkdir -p "$(dirname "$checkout")"

exec 9>"${checkout}.update.lock"
flock -n 9 || { echo "seed source update already running; skipping"; exit 0; }

if [ ! -d "$checkout/.git" ]; then
  git clone --filter=blob:none --sparse --branch master --single-branch \
    "$repo" "$checkout"
  git -C "$checkout" sparse-checkout set tasks/seeds tasks/manifests/seed_author
else
  # This checkout is pipeline-owned and read-only. Refuse local edits rather
  # than overwriting them or manufacturing an ambiguous seed snapshot.
  if [ -n "$(git -C "$checkout" status --porcelain)" ]; then
    echo "seed source checkout has local changes; refusing to update" >&2
    exit 1
  fi
  git -C "$checkout" pull --ff-only
fi
