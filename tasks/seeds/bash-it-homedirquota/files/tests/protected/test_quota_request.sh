#!/usr/bin/env bash

set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
tool=$project_root/bin/homedir-quota
fixture_source=$project_root/fixtures
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT

failures=0
checks=0

fail() {
  printf 'not ok - %s\n' "$1" >&2
  failures=$((failures + 1))
}

pass() {
  printf 'ok - %s\n' "$1"
}

assert_equal() {
  local expected=$1
  local actual=$2
  local label=$3
  checks=$((checks + 1))
  if [[ $actual == "$expected" ]]; then
    pass "$label"
  else
    fail "$label (expected <$expected>, got <$actual>)"
  fi
}

assert_unchanged() {
  local before=$1
  local after=$2
  local label=$3
  checks=$((checks + 1))
  if cmp -s -- "$before" "$after"; then
    pass "$label"
  else
    fail "$label"
    diff -u -- "$before" "$after" >&2 || true
  fi
}

fresh_data() {
  local name=$1
  local destination=$scratch/$name
  mkdir -p "$destination"
  cp -- "$fixture_source"/*.tsv "$destination/"
  printf '%s\n' "$destination"
}

quota_for() {
  local data_dir=$1
  local account=$2
  awk -F '\t' -v account="$account" '$1 == account { print $3 }' "$data_dir/quotas.tsv"
}

run_request() {
  local data_dir=$1
  local account=$2
  local requested=$3
  set +e
  output=$("$tool" "$data_dir" "$account" "$requested" 2>&1)
  status=$?
  set -e
}

data=$(fresh_data exact)
awk -F '\t' '$1 != "alice"' "$data/quotas.tsv" > "$scratch/exact.other.before"
run_request "$data" alice 110
assert_equal 0 "$status" "an eligible request succeeds"
assert_equal \
  "decision=applied account=alice requested_gib=110 current_gib=80 usage_gib=76 approval_ceiling_gib=125 capacity_ceiling_gib=140 exception_ceiling_gib=110 effective_ceiling_gib=110 limiting_factor=previous_exception applied_gib=110" \
  "$output" \
  "an applied request reports complete evidence"
assert_equal 110 "$(quota_for "$data" alice)" "the exact requested limit is stored"
awk -F '\t' '$1 != "alice"' "$data/quotas.tsv" > "$scratch/exact.other.after"
assert_unchanged "$scratch/exact.other.before" "$scratch/exact.other.after" \
  "an apply preserves every unrelated account"

data=$(fresh_data approval)
cp -- "$data/quotas.tsv" "$scratch/approval.before"
run_request "$data" bob 110
assert_equal 2 "$status" "a request above approval is denied"
assert_equal \
  "decision=denied account=bob requested_gib=110 current_gib=90 usage_gib=82 approval_ceiling_gib=105 capacity_ceiling_gib=150 exception_ceiling_gib=none effective_ceiling_gib=105 limiting_factor=approval applied_gib=none reason=request_exceeds_ceiling" \
  "$output" \
  "approval denial identifies its limiting evidence"
assert_unchanged "$scratch/approval.before" "$data/quotas.tsv" \
  "approval denial changes no account"

data=$(fresh_data capacity)
cp -- "$data/quotas.tsv" "$scratch/capacity.before"
run_request "$data" chandra 135
assert_equal 2 "$status" "a request above filesystem capacity is denied"
assert_equal \
  "decision=denied account=chandra requested_gib=135 current_gib=70 usage_gib=65 approval_ceiling_gib=150 capacity_ceiling_gib=130 exception_ceiling_gib=140 effective_ceiling_gib=130 limiting_factor=filesystem_capacity applied_gib=none reason=request_exceeds_ceiling" \
  "$output" \
  "capacity denial identifies its limiting evidence"
assert_unchanged "$scratch/capacity.before" "$data/quotas.tsv" \
  "capacity denial changes no account"

data=$(fresh_data exception)
cp -- "$data/quotas.tsv" "$scratch/exception.before"
run_request "$data" dana 90
assert_equal 2 "$status" "a request above a previous exception is denied"
assert_equal \
  "decision=denied account=dana requested_gib=90 current_gib=60 usage_gib=58 approval_ceiling_gib=130 capacity_ceiling_gib=120 exception_ceiling_gib=85 effective_ceiling_gib=85 limiting_factor=previous_exception applied_gib=none reason=request_exceeds_ceiling" \
  "$output" \
  "exception denial identifies its limiting evidence"
assert_unchanged "$scratch/exception.before" "$data/quotas.tsv" \
  "exception denial changes no account"

data=$(fresh_data usage)
cp -- "$data/quotas.tsv" "$scratch/usage.before"
run_request "$data" erin 120
assert_equal 2 "$status" "low fixture-backed usage denies an increase"
assert_equal \
  "decision=denied account=erin requested_gib=120 current_gib=100 usage_gib=50 approval_ceiling_gib=150 capacity_ceiling_gib=160 exception_ceiling_gib=140 effective_ceiling_gib=140 limiting_factor=previous_exception applied_gib=none utilization_percent=50 reason=usage_below_80_percent" \
  "$output" \
  "usage denial includes utilization evidence"
assert_unchanged "$scratch/usage.before" "$data/quotas.tsv" \
  "usage denial changes no account"

if (( failures > 0 )); then
  printf '%d of %d checks failed\n' "$failures" "$checks" >&2
  exit 1
fi

printf 'all %d checks passed\n' "$checks"
