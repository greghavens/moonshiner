#!/usr/bin/env bash

set -euo pipefail

readonly TEST_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
readonly PROJECT_DIR=$(cd "$TEST_DIR/.." && pwd)
readonly SCRIPT="$PROJECT_DIR/linuxenroll.sh"

SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT
readonly ROOT="$SANDBOX/root"
readonly MACHINE_ID='7d444840c1b94b8f8c67d9b62aa3f560'

fail() {
  printf 'not ok - %s\n' "$*" >&2
  exit 1
}

assert_eq() {
  local expected=$1
  local actual=$2
  local context=$3
  [[ $actual == "$expected" ]] ||
    fail "$context (expected '$expected', got '$actual')"
}

assert_lines() {
  local path=$1
  shift
  local expected="$SANDBOX/expected"
  printf '%s\n' "$@" > "$expected"
  cmp -s "$expected" "$path" || {
    printf 'content mismatch for %s\n' "$path" >&2
    diff -u "$expected" "$path" >&2 || true
    exit 1
  }
}

snapshot() {
  local relative
  while IFS= read -r -d '' relative; do
    stat -c 'metadata=%n|inode=%i|mtime=%y|mode=%a|size=%s' \
      "$ROOT/$relative"
    sha256sum "$ROOT/$relative"
  done < <(find "$ROOT" -type f -printf '%P\0' | LC_ALL=C sort -z)
}

age_files() {
  find "$ROOT" -type f -exec touch -d '@946684800' -- {} +
}

mkdir -p "$ROOT/etc/linuxenroll"
printf 'lab-17\n' > "$ROOT/etc/hostname"
printf '%s\n' "$MACHINE_ID" > "$ROOT/etc/machine-id"
printf '%s\n' 'fixture-certificate-bytes' > "$ROOT/etc/linuxenroll/device.pem"

output=$(
  bash "$SCRIPT" enroll \
    --root "$ROOT" \
    --certificate /etc/linuxenroll/device.pem \
    --owner team=platform \
    --owner cost-center=42
)
assert_eq enrolled "$output" "initial enrollment result"

assert_lines "$ROOT/var/lib/linuxenroll/enrollment.conf" \
  'service=moon-management' \
  'hostname=lab-17' \
  "machine_id=$MACHINE_ID" \
  'certificate=/etc/linuxenroll/device.pem'
assert_lines "$ROOT/etc/linuxenroll/baseline-packages" \
  ca-certificates curl jq rsyslog
assert_lines "$ROOT/etc/linuxenroll/owner.tags" \
  'cost-center=42' 'team=platform'
assert_lines "$ROOT/etc/rsyslog.d/60-linuxenroll.conf" \
  'local5.*    /var/log/linuxenroll-events.log'
assert_lines "$ROOT/var/lib/moon-management/hosts/$MACHINE_ID.record" \
  'service=moon-management' \
  'hostname=lab-17' \
  "machine_id=$MACHINE_ID" \
  'certificate=/etc/linuxenroll/device.pem' \
  'package=ca-certificates' \
  'package=curl' \
  'package=jq' \
  'package=rsyslog' \
  'owner=cost-center=42' \
  'owner=team=platform'
assert_lines "$ROOT/var/log/linuxenroll.log" \
  "enrolled host=lab-17 machine_id=$MACHINE_ID"
[[ $(< "$ROOT/var/lib/linuxenroll/desired.sha256") =~ ^[0-9a-f]{64}$ ]] ||
  fail "desired-state fingerprint is not a SHA-256 digest"
[[ $(stat -c '%a' "$ROOT/var/lib/linuxenroll/enrollment.conf") == 600 ]] ||
  fail "enrollment state permissions are not 600"
[[ $(stat -c '%a' "$ROOT/etc/linuxenroll/baseline-packages") == 644 ]] ||
  fail "baseline package permissions are not 644"

age_files
before_identical=$(snapshot)
output=$(
  bash "$SCRIPT" enroll \
    --owner cost-center=42 \
    --certificate /etc/linuxenroll/device.pem \
    --root "$ROOT" \
    --owner team=platform
)
after_identical=$(snapshot)
assert_eq 'already enrolled' "$output" "identical enrollment result"
assert_eq "$before_identical" "$after_identical" "identical enrollment changed files"

old_fingerprint=$(< "$ROOT/var/lib/linuxenroll/desired.sha256")
output=$(
  bash "$SCRIPT" enroll \
    --root "$ROOT" \
    --certificate /etc/linuxenroll/device.pem \
    --owner team=security \
    --owner cost-center=42
)
assert_eq updated "$output" "changed owner tag must reconcile"
assert_lines "$ROOT/etc/linuxenroll/owner.tags" \
  'cost-center=42' 'team=security'
assert_lines "$ROOT/var/lib/moon-management/hosts/$MACHINE_ID.record" \
  'service=moon-management' \
  'hostname=lab-17' \
  "machine_id=$MACHINE_ID" \
  'certificate=/etc/linuxenroll/device.pem' \
  'package=ca-certificates' \
  'package=curl' \
  'package=jq' \
  'package=rsyslog' \
  'owner=cost-center=42' \
  'owner=team=security'
assert_lines "$ROOT/var/log/linuxenroll.log" \
  "enrolled host=lab-17 machine_id=$MACHINE_ID" \
  "updated host=lab-17 machine_id=$MACHINE_ID"
new_fingerprint=$(< "$ROOT/var/lib/linuxenroll/desired.sha256")
[[ $new_fingerprint != "$old_fingerprint" ]] ||
  fail "owner tag change did not change desired-state fingerprint"

age_files
before_reconciled_rerun=$(snapshot)
output=$(
  bash "$SCRIPT" enroll \
    --root "$ROOT" \
    --owner cost-center=42 \
    --owner team=security \
    --certificate /etc/linuxenroll/device.pem
)
after_reconciled_rerun=$(snapshot)
assert_eq 'already enrolled' "$output" "reconciled enrollment rerun result"
assert_eq "$before_reconciled_rerun" "$after_reconciled_rerun" \
  "reconciled enrollment rerun changed files"

output=$(bash "$SCRIPT" unenroll --root "$ROOT")
assert_eq unenrolled "$output" "unenrollment result"

for removed in \
  "$ROOT/var/lib/linuxenroll/enrollment.conf" \
  "$ROOT/var/lib/linuxenroll/desired.sha256" \
  "$ROOT/etc/linuxenroll/baseline-packages" \
  "$ROOT/etc/linuxenroll/owner.tags" \
  "$ROOT/etc/rsyslog.d/60-linuxenroll.conf" \
  "$ROOT/var/lib/moon-management/hosts/$MACHINE_ID.record"; do
  [[ ! -e $removed ]] || fail "unenrollment left managed path: $removed"
done

assert_lines "$ROOT/etc/hostname" 'lab-17'
assert_lines "$ROOT/etc/machine-id" "$MACHINE_ID"
assert_lines "$ROOT/etc/linuxenroll/device.pem" 'fixture-certificate-bytes'
assert_lines "$ROOT/var/log/linuxenroll.log" \
  "enrolled host=lab-17 machine_id=$MACHINE_ID" \
  "updated host=lab-17 machine_id=$MACHINE_ID" \
  "unenrolled host=lab-17 machine_id=$MACHINE_ID"

age_files
before_second_unenroll=$(snapshot)
output=$(bash "$SCRIPT" unenroll --root "$ROOT")
after_second_unenroll=$(snapshot)
assert_eq 'already unenrolled' "$output" "second unenrollment result"
assert_eq "$before_second_unenroll" "$after_second_unenroll" \
  "second unenrollment changed files"

if bash "$SCRIPT" enroll \
  --root "$ROOT" \
  --certificate /etc/linuxenroll/missing.pem \
  --owner team=security >"$SANDBOX/missing.out" 2>"$SANDBOX/missing.err"; then
  fail "enrollment accepted a missing certificate"
fi
[[ ! -e "$ROOT/var/lib/linuxenroll/enrollment.conf" ]] ||
  fail "failed enrollment recreated state"

printf 'ok - linux enrollment lifecycle\n'
