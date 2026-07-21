#!/usr/bin/env bash

set -uo pipefail
export LC_ALL=C

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
program=$project_root/bin/configarchive
transport=$project_root/shims/local-device
fixture_root=$project_root/tests/fixtures
expected_root=$project_root/tests/expected
test_tmp=$(mktemp -d)
trap 'rm -rf "$test_tmp"' EXIT HUP INT TERM

checks=0
failures=0

ok() {
  checks=$((checks + 1))
  printf 'ok %d - %s\n' "$checks" "$1"
}

not_ok() {
  checks=$((checks + 1))
  failures=$((failures + 1))
  printf 'not ok %d - %s\n' "$checks" "$1"
}

check() {
  description=$1
  shift
  if "$@"; then
    ok "$description"
  else
    not_ok "$description"
  fi
}

check_file() {
  description=$1
  expected=$2
  actual=$3
  if cmp -s "$expected" "$actual"; then
    ok "$description"
  else
    not_ok "$description"
  fi
}

write_inventory() {
  inventory_path=$1
  shift
  : > "$inventory_path"
  while (( $# )); do
    printf '%s\t%s\n' "$1" "$2" >> "$inventory_path"
    shift 2
  done
}

run_collection() {
  fixtures=$1
  inventory=$2
  archive=$3
  stdout_file=$4
  stderr_file=$5
  CONFIGARCHIVE_FIXTURE_ROOT=$fixtures \
    "$program" "$inventory" "$archive" "$transport" \
    > "$stdout_file" 2> "$stderr_file"
}

archive=$test_tmp/archive
inventory=$test_tmp/inventory.tsv
stdout_file=$test_tmp/stdout
stderr_file=$test_tmp/stderr

write_inventory "$inventory" core-a core-a core-b core-b
run_collection "$fixture_root/v1" "$inventory" "$archive" "$stdout_file" "$stderr_file"
initial_status=$?
check 'initial collection succeeds' test "$initial_status" -eq 0
check_file 'volatile lines are removed from core-a' \
  "$expected_root/core-a-v1.config" "$archive/core-a/latest.conf"
check_file 'volatile lines are removed from core-b' \
  "$expected_root/core-b-v1.config" "$archive/core-b/latest.conf"

hash_a_v1=$(sha256sum "$expected_root/core-a-v1.config")
hash_a_v1=${hash_a_v1%% *}
hash_b_v1=$(sha256sum "$expected_root/core-b-v1.config")
hash_b_v1=${hash_b_v1%% *}
check 'core-a v1 is content-addressed' \
  cmp -s "$expected_root/core-a-v1.config" "$archive/core-a/versions/$hash_a_v1.conf"
check 'core-b hash pointer is recorded' \
  grep -Fxq "$hash_b_v1" "$archive/core-b/latest.sha256"

write_inventory "$inventory" core-a core-a
run_collection "$fixture_root/v2" "$inventory" "$archive" "$stdout_file" "$stderr_file"
update_status=$?
check 'changed collection succeeds' test "$update_status" -eq 0
check_file 'latest snapshot advances after a successful collection' \
  "$expected_root/core-a-v2.config" "$archive/core-a/latest.conf"

hash_a_v2=$(sha256sum "$expected_root/core-a-v2.config")
hash_a_v2=${hash_a_v2%% *}
diff_path=$archive/core-a/changes/$hash_a_v1..$hash_a_v2.diff
check 'a hash-pair diff is stored' test -f "$diff_path"
check 'diff records the removed line' grep -Fqx -- '- description lab-uplink' "$diff_path"
check 'diff records the added line' grep -Fqx -- '+ description production-uplink' "$diff_path"

before_b_latest=$test_tmp/core-b.latest.conf
before_b_hash=$test_tmp/core-b.latest.sha256
before_b_versions=$test_tmp/core-b.versions
before_b_diffs=$test_tmp/core-b.changes
cp "$archive/core-b/latest.conf" "$before_b_latest"
cp "$archive/core-b/latest.sha256" "$before_b_hash"
cp -R "$archive/core-b/versions" "$before_b_versions"
cp -R "$archive/core-b/changes" "$before_b_diffs"

write_inventory "$inventory" core-a core-a core-b core-b core-c core-c
run_collection "$fixture_root/outage" "$inventory" "$archive" "$stdout_file" "$stderr_file"
outage_status=$?
check 'an unreachable device makes the run nonzero' test "$outage_status" -ne 0
check 'the run reports the unreachable device' \
  grep -Fxq $'unreachable\tcore-b' "$archive/run.report"
check 'collection continues after an unreachable device' \
  grep -Eq $'^created\tcore-c\t[0-9a-f]{64}$' "$archive/run.report"
check_file 'an unreachable device keeps its last-known-good snapshot' \
  "$before_b_latest" "$archive/core-b/latest.conf"
check_file 'an unreachable device keeps its hash pointer unchanged' \
  "$before_b_hash" "$archive/core-b/latest.sha256"
check 'an unreachable device keeps all version objects unchanged' \
  diff -r "$before_b_versions" "$archive/core-b/versions"
check 'an unreachable device keeps all diffs unchanged' \
  diff -r "$before_b_diffs" "$archive/core-b/changes"

write_inventory "$inventory" core-a core-a
run_collection "$fixture_root/outage" "$inventory" "$archive" "$stdout_file" "$stderr_file"
unchanged_status=$?
check 'repeat collection succeeds' test "$unchanged_status" -eq 0
check 'repeat collection is reported as unchanged' \
  grep -Eq $'^unchanged\tcore-a\t[0-9a-f]{64}$' "$archive/run.report"

if (( failures )); then
  printf '1..%d\n' "$checks"
  printf '# %d of %d checks failed\n' "$failures" "$checks"
  exit 1
fi

printf '1..%d\n' "$checks"
exit 0
