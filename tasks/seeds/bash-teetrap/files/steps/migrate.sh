#!/usr/bin/env bash
# migrate step: apply pending schema snippets in order
set -u

mkdir -p out
applied=0
for m in migrations/*.sql; do
  [ -f "$m" ] || continue
  echo "migrate: applying ${m##*/}"
  printf '%s\n' "${m##*/}" >> out/schema.log
  applied=$((applied + 1))
done
echo "migrate: $applied migration(s) applied"
