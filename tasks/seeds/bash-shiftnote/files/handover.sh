#!/usr/bin/env bash
# handover.sh -- render the end-of-shift handover text from the events log.
# The wiki importer ingests the OPEN ITEMS block as tab-separated rows, so
# the exact layout of the report matters.
set -u
LC_ALL=C
export LC_ALL

if [ $# -ne 4 ]; then
  echo "usage: handover.sh <events.tsv> <shift-label> <oncall> <out.txt>" >&2
  exit 2
fi

events=$1
label=$2
oncall=$3
out=$4

if [ ! -f "$events" ]; then
  echo "handover: no such events file: $events" >&2
  exit 2
fi

declare -A open=()
notes=()
while IFS=$'\t' read -r stamp code note; do
  [ -n "$stamp" ] || continue
  case $code in
    OPEN-*)
      open[$code]=$(( ${open[$code]:-0} + 1 ))
      ;;
  esac
  if [ -n "$note" ]; then
    notes+=("$stamp  $note")
  fi
done < "$events"

{
  echo "SHIFT HANDOVER -- $label\n"
  echo "OPEN ITEMS"
  if [ "${#open[@]}" -eq 0 ]; then
    echo "(none)"
  else
    while IFS= read -r code; do
      row="$code\t${open[$code]}"
      printf '%s\n' "$row"
    done < <(printf '%s\n' "${!open[@]}" | sort)
  fi
  echo
  echo "NOTES"
  if [ "${#notes[@]}" -eq 0 ]; then
    echo "(none)"
  else
    for n in "${notes[@]}"; do
      printf -- '- %s\n' "$n"
    done
  fi
  echo
  footer='Questions: page $oncall via the rota.'
  printf '%s\n' "$footer"
} > "$out"

echo "handover written: $out"
