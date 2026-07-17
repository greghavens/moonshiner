#!/usr/bin/env bash
# nightly_report.sh — summarize one night's receiving-scan log.
#
# Scan events look like:
#   2026-07-12T22:04:11 scan depot=east sku=AA-1042 qty=3
# Heartbeats, notes and scanner errors share the log; only scan events count.
set -u
LC_ALL=C
export LC_ALL

if [[ $# -ne 1 ]]; then
  echo "usage: nightly_report.sh <scan-log>" >&2
  exit 64
fi
log=$1
if [[ ! -f "$log" || ! -r "$log" ]]; then
  echo "nightly_report.sh: cannot read: $log" >&2
  exit 66
fi

total_scans=0
total_items=0
declare -A depot_items=()

grep ' scan ' "$log" | while IFS=' ' read -r _ts _kind f_depot f_sku f_qty; do
  depot=${f_depot#depot=}
  sku=${f_sku#sku=}
  qty=${f_qty#qty=}
  case $qty in
    '' | *[!0-9]*)
      printf 'nightly_report.sh: skipped unparsable scan: %s\n' \
        "$_ts $_kind $f_depot $f_sku $f_qty" >&2
      continue
      ;;
  esac
  printf 'handled %s x%s\n' "$sku" "$qty"
  total_scans=$((total_scans + 1))
  total_items=$((total_items + qty))
  depot_items[$depot]=$(( ${depot_items[$depot]:-0} + qty ))
done

printf '%s\n' '-- nightly totals --'
printf 'scans: %d\n' "$total_scans"
printf 'items: %d\n' "$total_items"
printf 'depots:\n'
for d in "${!depot_items[@]}"; do
  printf '  %s: %d\n' "$d" "${depot_items[$d]}"
done | LC_ALL=C sort
