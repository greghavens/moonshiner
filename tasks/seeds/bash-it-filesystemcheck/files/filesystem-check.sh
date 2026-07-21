#!/usr/bin/env bash

# Turn a captured filesystem-check transcript into an operator plan. This
# program deliberately does not probe or modify the named device.
set -u
LC_ALL=C
export LC_ALL

usage() {
  printf 'usage: filesystem-check.sh --device DEVICE --mountpoint MOUNTPOINT --status STATUS REPORT\n' >&2
  exit 64
}

[ "$#" -eq 7 ] || usage
[ "$1" = "--device" ] || usage
device=$2
[ "$3" = "--mountpoint" ] || usage
mountpoint=$4
[ "$5" = "--status" ] || usage
status=$6
report=$7

case $status in
  ''|*[!0-9]*)
    printf 'filesystem-check.sh: status must be an integer from 0 to 255\n' >&2
    exit 64
    ;;
esac
if [ "$status" -gt 255 ]; then
  printf 'filesystem-check.sh: status must be an integer from 0 to 255\n' >&2
  exit 64
fi
if [ ! -f "$report" ] || [ ! -r "$report" ]; then
  printf 'filesystem-check.sh: report is not readable: %s\n' "$report" >&2
  exit 66
fi

# Explicit error evidence wins over a clean-looking line so a contradictory or
# concatenated transcript cannot be classified as safe.
if grep -Fq 'UNEXPECTED INCONSISTENCY; RUN fsck MANUALLY' "$report" ||
   grep -Fq 'Filesystem errors left uncorrected' "$report"; then
  classification=offline-repair
elif [ "$status" -eq 0 ] && grep -Eq '(^|: )[Cc]lean([,[:space:]]|$)' "$report"; then
  classification=online-safe
else
  classification=offline-repair
fi

case $classification in
  online-safe)
    printf '%s\n' \
      'classification=online-safe' \
      'evidence=successful check explicitly reports the filesystem clean' \
      'automatic_repair=not-needed' \
      'backup_prerequisite=none for this no-change result; retain the normal verified-backup policy' \
      "procedure=leave $mountpoint in service; do not run a repair"
    exit 0
    ;;
  offline-repair)
    printf '%s\n' \
      'classification=offline-repair' \
      'evidence=checker explicitly reports an uncorrected filesystem inconsistency' \
      'automatic_repair=refused' \
      "backup_prerequisite=before repair, create and verify a restorable backup or block-level image of $device" \
      "procedure=schedule downtime; stop writers; unmount $mountpoint; verify $device is unmounted; identify the filesystem type and its supported checker; run that checker interactively from a console without automatic yes-to-all; review every prompt; rerun read-only verification; remount $mountpoint; validate service"
    exit 2
    ;;
  evidence-insufficient)
    printf '%s\n' \
      'classification=evidence-insufficient' \
      'evidence=captured status and output do not prove a clean filesystem or an uncorrected inconsistency' \
      'automatic_repair=refused' \
      'backup_prerequisite=do not repair; first identify the filesystem and device, confirm mount state, and obtain a verified restorable backup' \
      'procedure=collect the complete checker output and exit status; inspect kernel and storage errors; identify filesystem-specific tooling; escalate for operator review'
    exit 3
    ;;
esac
