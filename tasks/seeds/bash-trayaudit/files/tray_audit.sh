#!/usr/bin/env bash
# tray_audit.sh -- morning walk-through summary for the propagation house.
# Manifest lines: <tray-id> <kind> <days-since-sow>; comments start with #.
# usage: tray_audit.sh <manifest>
set -u

if [ $# -ne 1 ]; then
  echo "usage: tray_audit.sh <manifest>" >&2
  exit 64
fi

manifest=$1
if [ ! -r "$manifest" ]; then
  echo "tray_audit.sh: cannot read: $manifest" >&2
  exit 66
fi

limit=21
total=0
overdue=0
plugs=0
flats=0
cells=0
other=0

while read -r tray kind days; do
  case "$tray" in
    ''|\#*) continue ;;
  esac
  total=$((total + 1))
  if [ "$days" -ge "$limit" ]; then
    if [ "$kind" = plug ]; then
      echo "overdue: $tray (plug tray, day $days) - pot on or toss"
    else
      echo "overdue: $tray ($kind, day $days)"
    overdue=$((overdue + 1))
  fi
  case "$kind" in
    plug) plugs=$((plugs + 1)) ;;
    flat) flats=$((flats + 1))
    cell) cells=$((cells + 1)) ;;
    *) other=$((other + 1)) ;;
  esac
done < "$manifest"

echo "trays checked: $total"
echo "overdue: $overdue"
echo "by kind: plug=$plugs flat=$flats cell=$cells other=$other"
