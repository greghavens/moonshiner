#!/usr/bin/env bash

set -u
set -o pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
runner="$root/runbook-smoke"
tests_run=0
tmp=$(mktemp -d "${TMPDIR:-/tmp}/test-runbook-smoke.XXXXXX") || exit 1
trap 'rm -rf -- "$tmp"' EXIT HUP INT TERM

fail() {
    printf 'not ok %d - %s\n' "$tests_run" "$1" >&2
    exit 1
}

assert_status() {
    local want=$1
    local got=$2
    local label=$3
    [[ $got -eq $want ]] || fail "$label: status $got, expected $want"
}

assert_equal() {
    local want=$1
    local got=$2
    local label=$3
    [[ $got == "$want" ]] || {
        printf '%s\n' "--- expected" "$want" "--- actual" "$got" >&2
        fail "$label: output differs"
    }
}

run_capture() {
    local destination=$1
    shift
    set +e
    CAPTURED_OUTPUT=$("$@" 2>&1)
    CAPTURED_STATUS=$?
    set -e
    printf -v "$destination" '%s' "$CAPTURED_OUTPUT"
}

set -e

((tests_run += 1))
sentinel="$tmp/destructive-ran"
export RUNBOOK_DESTRUCTIVE_SENTINEL=$sentinel
run_capture sample bash "$runner" --today 2026-07-20 "$root/runbooks/service-recovery.runbook"
assert_status 1 "$CAPTURED_STATUS" 'stale sample'
expected_sample='PASS service-recovery:check-status
STALE service-recovery:render-plan review-by=2026-06-30
PASS service-recovery:render-plan
SKIP service-recovery:reset-production safety=destructive
SUMMARY pass=2 stale=1 missing=0 failed=0 skipped=1'
assert_equal "$expected_sample" "$sample" 'stale sample'
[[ ! -e $sentinel ]] || fail 'destructive step executed'
printf 'ok %d - safe gating and stale reporting\n' "$tests_run"

case_dir="$tmp/cases"
mkdir -p -- "$case_dir/fixture/bin" "$case_dir/fixture/expected" "$case_dir/fixture/config" "$case_dir/scratch"
cp -- "$root/fixtures/service-recovery/bin/status.sh" "$case_dir/fixture/bin/status.sh"
cp -- "$root/fixtures/service-recovery/expected/status.out" "$case_dir/fixture/expected/status.out"
cp -- "$root/fixtures/service-recovery/config/service.env" "$case_dir/fixture/config/service.env"
mkdir -p -- "$case_dir/fixture/state"
cp -- "$root/fixtures/service-recovery/state/service.state" "$case_dir/fixture/state/service.state"

((tests_run += 1))
printf '%s\n' 'step|reviewed-today|safe|fixture|bin/status.sh|expected/status.out|config/service.env,state/service.state|2026-07-20|-' > "$case_dir/current.runbook"
run_capture current_output env TMPDIR="$case_dir/scratch" bash "$runner" --today 2026-07-20 "$case_dir/current.runbook"
assert_status 0 "$CAPTURED_STATUS" 'current clean step'
expected_current='PASS current:reviewed-today
SUMMARY pass=1 stale=0 missing=0 failed=0 skipped=0'
assert_equal "$expected_current" "$current_output" 'current clean step'
[[ -z $(find "$case_dir/scratch" -mindepth 1 -print -quit) ]] || fail 'successful harness scratch directory leaked'
printf 'ok %d - current boundary and successful cleanup\n' "$tests_run"

((tests_run += 1))
printf '%s\n' 'step|needs-token|safe|fixture|bin/status.sh|expected/status.out|config/token,state/service.state|2026-12-31|-' > "$case_dir/missing.runbook"
run_capture missing_output env TMPDIR="$case_dir/scratch" bash "$runner" --today 2026-07-20 "$case_dir/missing.runbook"
assert_status 1 "$CAPTURED_STATUS" 'missing prerequisite'
expected_missing='MISSING missing:needs-token prerequisite=config/token
SUMMARY pass=0 stale=0 missing=1 failed=0 skipped=0'
assert_equal "$expected_missing" "$missing_output" 'missing prerequisite'
[[ -z $(find "$case_dir/scratch" -mindepth 1 -print -quit) ]] || fail 'harness scratch directory leaked'
printf 'ok %d - missing prerequisite and harness cleanup\n' "$tests_run"

((tests_run += 1))
printf '%s\n' 'not the expected report' > "$case_dir/fixture/expected/wrong.out"
printf '%s\n' 'step|wrong-report|safe|fixture|bin/status.sh|expected/wrong.out|config/service.env,state/service.state|2026-12-31|-' > "$case_dir/mismatch.runbook"
run_capture mismatch_output bash "$runner" --today 2026-07-20 "$case_dir/mismatch.runbook"
assert_status 1 "$CAPTURED_STATUS" 'output mismatch'
expected_mismatch='FAIL mismatch:wrong-report output
SUMMARY pass=0 stale=0 missing=0 failed=1 skipped=0'
assert_equal "$expected_mismatch" "$mismatch_output" 'output mismatch'
printf 'ok %d - expected output validation\n' "$tests_run"

((tests_run += 1))
printf '%s\n' '#!/usr/bin/env bash' ': > residue.tmp' "printf 'clean me\\n'" > "$case_dir/fixture/bin/leak.sh"
printf '%s\n' 'clean me' > "$case_dir/fixture/expected/leak.out"
printf '%s\n' 'step|leaky-check|safe|fixture|bin/leak.sh|expected/leak.out|-|2026-12-31|residue.tmp' > "$case_dir/cleanup.runbook"
run_capture cleanup_output bash "$runner" --today 2026-07-20 "$case_dir/cleanup.runbook"
assert_status 1 "$CAPTURED_STATUS" 'cleanup validation'
expected_cleanup='FAIL cleanup:leaky-check cleanup=residue.tmp
SUMMARY pass=0 stale=0 missing=0 failed=1 skipped=0'
assert_equal "$expected_cleanup" "$cleanup_output" 'cleanup validation'
printf 'ok %d - per-step cleanup validation\n' "$tests_run"

printf '1..%d\n' "$tests_run"
