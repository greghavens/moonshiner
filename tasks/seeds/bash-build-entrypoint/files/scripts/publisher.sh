#!/usr/bin/env bash
# Internal handoff: <route> <channel> <label> <artifact>...
set -eu

[ "$#" -ge 4 ] || { printf 'publisher: invalid internal invocation\n' >&2; exit 70; }
route=$1
channel=$2
label=$3
shift 3

printf 'route=%s\n' "$route"
printf 'channel=%s\n' "$channel"
if [ -n "$label" ]; then
  printf 'label=%s\n' "$label"
else
  printf 'label=-\n'
fi
printf 'artifact-count=%s\n' "$#"
for artifact in "$@"; do
  printf 'artifact=%s\n' "$artifact"
done
