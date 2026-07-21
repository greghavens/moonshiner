#!/usr/bin/env bash
set -u
LC_ALL=C
export LC_ALL

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
script=$root/filesystem-check.sh
tmp=$(mktemp -d "${TMPDIR:-/tmp}/filesystem-check-test.XXXXXX") || exit 1
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

# Put logging stand-ins ahead of host maintenance tools. The planner must only
# print instructions; even the explicit-corruption case must execute none of
# these commands.
fake_bin=$tmp/fake-bin
command_log=$tmp/maintenance-commands.log
mkdir "$fake_bin"
for tool in fsck e2fsck mount umount; do
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    "printf 'called:$tool\\n' >> \"\$COMMAND_LOG\"" \
    'exit 99' > "$fake_bin/$tool"
  chmod +x "$fake_bin/$tool"
done
COMMAND_LOG=$command_log
export COMMAND_LOG
PATH=$fake_bin:$PATH
export PATH

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

run_plan() {
  name=$1
  status=$2
  expected_status=$3
  report_text=$4
  expected_text=$5

  printf '%s\n' "$report_text" > "$tmp/$name.report"
  bash "$script" \
    --device /dev/vdb1 \
    --mountpoint /srv/data \
    --status "$status" \
    "$tmp/$name.report" > "$tmp/$name.out" 2> "$tmp/$name.err"
  actual_status=$?

  [ "$actual_status" -eq "$expected_status" ] ||
    fail "$name exited $actual_status, expected $expected_status"
  [ ! -s "$tmp/$name.err" ] || fail "$name wrote to stderr"
  printf '%s\n' "$expected_text" > "$tmp/$name.expected"
  if ! cmp -s "$tmp/$name.expected" "$tmp/$name.out"; then
    printf '%s\n' "--- $name expected ---" >&2
    sed -n '1,120p' "$tmp/$name.expected" >&2
    printf '%s\n' "--- $name actual ---" >&2
    sed -n '1,120p' "$tmp/$name.out" >&2
    fail "$name plan differed"
  fi
}

run_invalid() {
  name=$1
  expected_status=$2
  expected_error=$3
  shift 3

  bash "$script" "$@" > "$tmp/$name.out" 2> "$tmp/$name.err"
  actual_status=$?

  [ "$actual_status" -eq "$expected_status" ] ||
    fail "$name exited $actual_status, expected $expected_status"
  [ ! -s "$tmp/$name.out" ] || fail "$name wrote to stdout"
  printf '%s\n' "$expected_error" > "$tmp/$name.expected-err"
  cmp -s "$tmp/$name.expected-err" "$tmp/$name.err" ||
    fail "$name validation error differed"
}

insufficient_plan='classification=evidence-insufficient
evidence=captured status and output do not prove a clean filesystem or an uncorrected inconsistency
automatic_repair=refused
backup_prerequisite=do not repair; first identify the filesystem and device, confirm mount state, and obtain a verified restorable backup
procedure=collect the complete checker output and exit status; inspect kernel and storage errors; identify filesystem-specific tooling; escalate for operator review'

run_plan clean 0 0 \
  'fsck from util-linux 2.39
/dev/vdb1: clean, 42/1024 files, 300/4096 blocks' \
  'classification=online-safe
evidence=successful check explicitly reports the filesystem clean
automatic_repair=not-needed
backup_prerequisite=none for this no-change result; retain the normal verified-backup policy
procedure=leave /srv/data in service; do not run a repair'

run_plan corruption 4 2 \
  'Inode 17 has an invalid extent.
UNEXPECTED INCONSISTENCY; RUN fsck MANUALLY.
        (i.e., without -a or -p options)' \
  'classification=offline-repair
evidence=checker explicitly reports an uncorrected filesystem inconsistency
automatic_repair=refused
backup_prerequisite=before repair, create and verify a restorable backup or block-level image of /dev/vdb1
procedure=schedule downtime; stop writers; unmount /srv/data; verify /dev/vdb1 is unmounted; identify the filesystem type and its supported checker; run that checker interactively from a console without automatic yes-to-all; review every prompt; rerun read-only verification; remount /srv/data; validate service'

# A refusal to check a mounted filesystem says nothing about its health. A
# nonzero checker status must not turn this into a destructive repair recipe.
run_plan mounted_refusal 8 3 \
  '/dev/vdb1 is mounted.
e2fsck: Cannot continue, aborting.' \
  "$insufficient_plan"

# An I/O/open failure is storage evidence, but not proof that an offline fsck
# repair is appropriate. The insufficient-evidence plan contains no fsck call.
run_plan open_failure 8 3 \
  'fsck.ext4: Input/output error while trying to open /dev/vdb1
Possibly non-existent device?' \
  "$insufficient_plan"

# The default is fail-closed: neither a nonzero status nor incomplete or
# unfamiliar output proves that offline repair is appropriate.
run_plan nonzero_status_only 8 3 '' "$insufficient_plan"

run_plan truncated_output 0 3 \
  'fsck from util-linux 2.39
Pass 1: Checking inodes, blocks, and sizes' \
  "$insufficient_plan"

run_plan unrecognized_output 0 3 \
  'The filesystem check was requested by an operator.' \
  "$insufficient_plan"

# A clean-looking summary is safe only when the captured status is zero.
run_plan nonzero_clean 1 3 \
  '/dev/vdb1: clean, 42/1024 files, 300/4096 blocks' \
  "$insufficient_plan"

# Explicit corruption takes precedence over a stale clean summary.
run_plan contradictory 0 2 \
  '/dev/vdb1: clean, 42/1024 files, 300/4096 blocks
Filesystem errors left uncorrected' \
  'classification=offline-repair
evidence=checker explicitly reports an uncorrected filesystem inconsistency
automatic_repair=refused
backup_prerequisite=before repair, create and verify a restorable backup or block-level image of /dev/vdb1
procedure=schedule downtime; stop writers; unmount /srv/data; verify /dev/vdb1 is unmounted; identify the filesystem type and its supported checker; run that checker interactively from a console without automatic yes-to-all; review every prompt; rerun read-only verification; remount /srv/data; validate service'

# Preserve the existing command-line validation and diagnostics while changing
# only the catch-all classification.
printf '%s\n' 'validation fixture' > "$tmp/validation.report"
run_invalid usage 64 \
  'usage: filesystem-check.sh --device DEVICE --mountpoint MOUNTPOINT --status STATUS REPORT'
run_invalid invalid_status 64 \
  'filesystem-check.sh: status must be an integer from 0 to 255' \
  --device /dev/vdb1 --mountpoint /srv/data --status invalid \
  "$tmp/validation.report"
run_invalid missing_report 66 \
  "filesystem-check.sh: report is not readable: $tmp/missing.report" \
  --device /dev/vdb1 --mountpoint /srv/data --status 0 \
  "$tmp/missing.report"

if grep -Fq 'fsck -' \
  "$tmp/mounted_refusal.out" \
  "$tmp/open_failure.out" \
  "$tmp/nonzero_status_only.out" \
  "$tmp/truncated_output.out" \
  "$tmp/unrecognized_output.out" \
  "$tmp/nonzero_clean.out"; then
  fail 'insufficient evidence emitted a repair command'
fi
[ ! -s "$command_log" ] || fail 'planner executed a filesystem maintenance command'

printf 'All protected filesystem-check tests passed\n'
