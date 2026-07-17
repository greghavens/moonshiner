#!/usr/bin/env bash
# offload.sh — pull a field-recorder card into the session vault and index it.
#
# Card layout: a flat directory of clips straight off the recorder, plus
# offload.conf describing what to skip and how to label the session:
#
#     skip=tmp,bak
#     labels=morning,field,b-roll
#
# Usage: offload.sh <carddir> <vaultdir>
set -u
LC_ALL=C
export LC_ALL

usage() {
  printf 'usage: offload.sh <carddir> <vaultdir>\n' >&2
  exit 64
}

[ "$#" -eq 2 ] || usage
card=$1
vault=$2
[ -d "$card" ] || { printf 'offload.sh: not a directory: %s\n' "$card" >&2; exit 66; }
conf="$card/offload.conf"
[ -f "$conf" ] || { printf 'offload.sh: missing offload.conf in %s\n' "$card" >&2; exit 66; }

mkdir -p "$vault"

skip_val=$(grep '^skip=' "$conf")
skip_val=${skip_val#skip=}
labels_val=$(grep '^labels=' "$conf")
labels_val=${labels_val#labels=}

# copy pass: everything on the card except the conf and the skip extensions
copied=()
for clip in $(find "$card" -maxdepth 1 -type f); do
  name=${clip##*/}
  [ "$name" = offload.conf ] && continue
  ext=${name##*.}
  skipit=no
  IFS=,
  for s in $skip_val; do
    [ "$ext" = "$s" ] && skipit=yes
  done
  [ "$skipit" = yes ] && continue
  cp $clip "$vault/$name"
  copied+=("$name")
done

# index pass: session labels, one line per clip, byte total
labels=()
for l in $labels_val; do
  labels+=("$l")
done

index="$vault/index.txt"
total=0
{
  printf 'labels: %s\n' "${labels[*]}"
  for name in "${copied[@]}"; do
    bytes=$(wc -c < "$vault/$name" 2>/dev/null)
    bytes=$((bytes + 0))
    total=$((total + bytes))
    printf '%s\t%s\n' "$name" "$bytes"
  done
  printf 'total\t%s\n' "$total"
} > "$index"

printf 'offloaded: %s (%s files, %s bytes)\n' "${copied[*]}" "${#copied[@]}" "$total"
