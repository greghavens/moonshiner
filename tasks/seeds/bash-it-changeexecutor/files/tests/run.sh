#!/usr/bin/env bash

set -u
set -o pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNNER=$ROOT/change-executor.sh
CHANGE_FIXTURE=$ROOT/fixtures/demo-change
APPROVAL_FIXTURE=$ROOT/fixtures/approval.token
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/change-executor-tests.XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
  printf '    %s\n' "$*" >&2
  return 1
}

assert_status() {
  local expected=$1 actual=$2
  [[ $actual == "$expected" ]] || fail "expected status $expected, got $actual"
}

assert_file_equals() {
  local path=$1 expected=$2 actual
  [[ -f $path ]] || {
    fail "missing file: $path"
    return
  }
  actual=$(<"$path")
  [[ $actual == "$expected" ]] || fail "unexpected contents of $path: [$actual]"
}

assert_absent() {
  local path=$1
  [[ ! -e $path ]] || fail "expected path to be absent: $path"
}

assert_journal_has() {
  local journal=$1 event=$2 detail=$3
  while IFS=$'\t' read -r actual_event actual_detail; do
    if [[ $actual_event == "$event" && $actual_detail == "$detail" ]]; then
      return 0
    fi
  done <"$journal"
  fail "journal is missing: $event<TAB>$detail"
}

assert_journal_lacks_event() {
  local journal=$1 unwanted=$2 event detail
  while IFS=$'\t' read -r event detail; do
    [[ $event != "$unwanted" ]] || {
      fail "journal unexpectedly contains event: $unwanted"
      return
    }
  done <"$journal"
}

new_case() {
  local name=$1
  CASE_DIR=$TMP_ROOT/$name
  WORK_DIR=$CASE_DIR/work
  STATE_DIR=$CASE_DIR/state
  mkdir -p "$WORK_DIR" "$STATE_DIR"
}

run_change() {
  "$RUNNER" \
    --change-dir "$CHANGE_FIXTURE" \
    --work-dir "$WORK_DIR" \
    --state-dir "$STATE_DIR" \
    --approval-file "$APPROVAL_FIXTURE" \
    "$@" >"$CASE_DIR/output.log" 2>&1
}

test_successful_change_is_ordered_and_journaled() {
  new_case success
  run_change
  local rc=$?

  assert_status 0 "$rc" || return
  assert_file_equals "$WORK_DIR/mutations.log" $'10-write-alpha\n20-write-beta\n30-write-gamma\n40-write-delta' || return
  assert_absent "$WORK_DIR/rollback.log" || return
  assert_journal_has "$STATE_DIR/journal.tsv" PRECHECK_OK demo-change || return
  assert_journal_has "$STATE_DIR/journal.tsv" APPROVAL_OK demo-change || return
  assert_journal_has "$STATE_DIR/journal.tsv" STEP_OK 40-write-delta || return
  assert_journal_has "$STATE_DIR/journal.tsv" VERIFY_OK demo-change || return
  assert_journal_has "$STATE_DIR/journal.tsv" COMPLETED demo-change
}

test_precheck_happens_before_mutation() {
  new_case precheck
  : >"$WORK_DIR/fail-precheck"
  run_change
  local rc=$?

  assert_status 41 "$rc" || return
  assert_absent "$WORK_DIR/mutations.log" || return
  assert_journal_has "$STATE_DIR/journal.tsv" PRECHECK_FAILED 41 || return
  assert_journal_lacks_event "$STATE_DIR/journal.tsv" STEP_START
}

test_explicit_approval_token_is_required() {
  new_case approval
  local bad_token=$CASE_DIR/wrong.token
  printf 'approve:some-other-change\n' >"$bad_token"

  "$RUNNER" \
    --change-dir "$CHANGE_FIXTURE" \
    --work-dir "$WORK_DIR" \
    --state-dir "$STATE_DIR" \
    --approval-file "$bad_token" >"$CASE_DIR/output.log" 2>&1
  local rc=$?

  assert_status 3 "$rc" || return
  assert_absent "$WORK_DIR/mutations.log" || return
  assert_journal_has "$STATE_DIR/journal.tsv" APPROVAL_REJECTED token-mismatch || return
  assert_journal_lacks_event "$STATE_DIR/journal.tsv" STEP_START
}

test_step_failure_stops_and_rolls_back_in_reverse() {
  new_case step-failure
  : >"$WORK_DIR/fail-step-30"
  run_change
  local rc=$?

  assert_status 43 "$rc" || return
  assert_absent "$WORK_DIR/alpha" || return
  assert_absent "$WORK_DIR/beta" || return
  assert_absent "$WORK_DIR/gamma" || return
  assert_absent "$WORK_DIR/delta" || return
  assert_file_equals "$WORK_DIR/rollback.log" $'20-write-beta\n10-write-alpha' || return
  assert_journal_has "$STATE_DIR/journal.tsv" STEP_FAILED 30-write-gamma:43 || return
  assert_journal_lacks_event "$STATE_DIR/journal.tsv" VERIFY_START || return
  if grep -Fq $'STEP_START\t40-write-delta' "$STATE_DIR/journal.tsv"; then
    fail 'step 40 ran after step 30 failed'
  fi
}

test_verification_failure_rolls_back_in_reverse() {
  new_case verify-failure
  : >"$WORK_DIR/fail-verify"
  run_change
  local rc=$?

  assert_status 55 "$rc" || return
  assert_absent "$WORK_DIR/alpha" || return
  assert_absent "$WORK_DIR/beta" || return
  assert_absent "$WORK_DIR/gamma" || return
  assert_absent "$WORK_DIR/delta" || return
  assert_file_equals "$WORK_DIR/rollback.log" $'40-write-delta\n30-write-gamma\n20-write-beta\n10-write-alpha' || return
  assert_journal_has "$STATE_DIR/journal.tsv" VERIFY_FAILED 55 || return
  assert_journal_has "$STATE_DIR/journal.tsv" ROLLED_BACK ok
}

test_resume_skips_completed_mutations() {
  new_case resume

  # Model a process interruption after step 10 was durably journaled and after
  # step 20 was announced but before its apply script began mutating anything.
  printf 'alpha\n' >"$WORK_DIR/alpha"
  printf '10-write-alpha\n' >"$WORK_DIR/mutations.log"
  printf '%s\n' \
    $'BEGIN\tdemo-change' \
    $'PRECHECK_OK\tdemo-change' \
    $'APPROVAL_OK\tdemo-change' \
    $'STEP_START\t10-write-alpha' \
    $'STEP_OK\t10-write-alpha' \
    $'STEP_START\t20-write-beta' >"$STATE_DIR/journal.tsv"

  run_change --resume
  local rc=$?

  assert_status 0 "$rc" || return
  assert_file_equals "$WORK_DIR/mutations.log" $'10-write-alpha\n20-write-beta\n30-write-gamma\n40-write-delta' || return
  assert_journal_has "$STATE_DIR/journal.tsv" RESUME demo-change || return
  assert_journal_has "$STATE_DIR/journal.tsv" STEP_SKIPPED 10-write-alpha || return
  assert_journal_has "$STATE_DIR/journal.tsv" COMPLETED demo-change
}

test_resume_requires_matching_nonterminal_journal() {
  new_case terminal-resume
  printf '%s\n' $'BEGIN\tdemo-change' $'COMPLETED\tdemo-change' >"$STATE_DIR/journal.tsv"
  run_change --resume
  local rc=$?

  assert_status 2 "$rc" || return
  assert_absent "$WORK_DIR/mutations.log"
}

tests=(
  test_successful_change_is_ordered_and_journaled
  test_precheck_happens_before_mutation
  test_explicit_approval_token_is_required
  test_step_failure_stops_and_rolls_back_in_reverse
  test_verification_failure_rolls_back_in_reverse
  test_resume_skips_completed_mutations
  test_resume_requires_matching_nonterminal_journal
)

passed=0
failed=0
for test_name in "${tests[@]}"; do
  printf 'TEST %s\n' "$test_name"
  if ("$test_name"); then
    printf '  PASS\n'
    ((passed++))
  else
    printf '  FAIL\n'
    ((failed++))
  fi
done

printf '\nRESULT: %d passed, %d failed\n' "$passed" "$failed"
((failed == 0))
