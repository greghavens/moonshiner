#!/usr/bin/env bash
# feedmix.sh — print the morning prep sheet for the barn feed round.
#
# usage: feedmix.sh CHART [stocked supplement ...]
#
# The chart is one horse per line, passwd-style colon fields:
#   name:scoops:base feed:supplement:note
# Supplements currently in the bin room are passed as arguments; anything a
# horse needs that is not stocked gets called out and listed at the end.

warn() {
  printf '%s\n' $@ >&2
}

if [ $# -lt 1 ]; then
  warn "usage: feedmix.sh CHART [supplement ...]"
  exit 2
fi

chart="$1"
shift
stocked=("$@")

if [ ! -r "$chart" ]; then
  warn "feedmix: cannot read chart $chart"
  exit 2
fi

print_row() {
  local out=""
  local sep=""
  local part
  for part in "$@"; do
    out="$out$sep$part"
    sep=" | "
  done
  printf '%s\n' "$out"
}

have_supplement() {
  if [[ " ${stocked[@]} " =~ " $1 " ]]; then
    return 0
  fi
  return 1
}

horses=0
missing=()

IFS=:
while read name scoops feed sup note; do
  [ -n "$name" ] || continue
  row=("$name" "$scoops scoops" "$feed")
  if [ -n "$sup" ]; then
    if have_supplement "$sup"; then
      row+=("plus $sup")
    else
      row+=("plus $sup (NOT IN BIN ROOM)")
      missing+=("$sup")
    fi
  fi
  if [ -n "$note" ]; then
    row+=("note: $note")
  fi
  print_row ${row[@]}
  horses=$((horses + 1))
done < "$chart"

printf 'fed %s horse(s)\n' "$horses"
if [ ${#missing[@]} -gt 0 ]; then
  printf 'short on: %s\n' "${missing[*]}"
  exit 3
fi
exit 0
