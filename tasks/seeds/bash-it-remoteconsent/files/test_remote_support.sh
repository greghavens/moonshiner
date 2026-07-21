#!/usr/bin/env bash

set -u

SCRIPT=$PWD/remote_support.sh
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/remote-consent.XXXXXX")
FAILURES=0

cleanup_tests() {
    rm -rf -- "$TEST_ROOT"
}
trap cleanup_tests EXIT

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    FAILURES=$((FAILURES + 1))
}

assert_eq() {
    local expected=$1 actual=$2 label=$3
    [[ $actual == "$expected" ]] || fail "$label (expected '$expected', got '$actual')"
}

assert_file_eq() {
    local expected=$1 file=$2 label=$3 actual
    actual=$(<"$file")
    [[ $actual == "$expected" ]] || fail "$label"
}

assert_absent() {
    local path=$1 label=$2
    [[ ! -e $path ]] || fail "$label"
}

make_ticket() {
    local file=$1 id=$2
    shift 2
    {
        printf 'ticket=%s\n' "$id"
        local action
        for action in "$@"; do
            printf 'allow=%s\n' "$action"
        done
    } >"$file"
}

make_consent() {
    local file=$1 id=$2 decision=$3
    printf 'ticket=%s\nconsent=%s\n' "$id" "$decision" >"$file"
}

test_consent_is_explicit() {
    local case_dir=$TEST_ROOT/consent rc
    mkdir -p "$case_dir/runtime"
    make_ticket "$case_dir/ticket" INC-101 status
    make_consent "$case_dir/consent" INC-101 no
    printf 'status\n' >"$case_dir/actions"

    bash "$SCRIPT" "$case_dir/ticket" "$case_dir/consent" "$case_dir/actions" \
        "$case_dir/audit" "$case_dir/runtime" >"$case_dir/out" 2>"$case_dir/err"
    rc=$?

    assert_eq 77 "$rc" 'missing consent status'
    assert_file_eq 'DENIED ticket=INC-101 reason=consent' "$case_dir/audit" 'consent denial audit'
    assert_absent "$case_dir/runtime/session-INC-101" 'denied consent created a session'
}

test_success_and_redaction() {
    local case_dir=$TEST_ROOT/success rc expected_audit
    mkdir -p "$case_dir/runtime"
    make_ticket "$case_dir/ticket" INC-202 status diagnostics
    make_consent "$case_dir/consent" INC-202 yes
    printf 'status\ndiagnostics\n' >"$case_dir/actions"

    bash "$SCRIPT" "$case_dir/ticket" "$case_dir/consent" "$case_dir/actions" \
        "$case_dir/audit" "$case_dir/runtime" >"$case_dir/out" 2>"$case_dir/err"
    rc=$?
    expected_audit=$'START ticket=INC-202\nACTION ticket=INC-202 command=status\nRESULT ticket=INC-202 command=status result=ok rc=0 output=service=ready\nACTION ticket=INC-202 command=diagnostics\nRESULT ticket=INC-202 command=diagnostics result=ok rc=0 output=host=demo api_key=[REDACTED] password=[REDACTED] token=[REDACTED]\nSTOP ticket=INC-202 reason=completed rc=0'

    assert_eq 0 "$rc" 'successful session status'
    assert_file_eq $'service=ready\nhost=demo api_key=[REDACTED] password=[REDACTED] token=[REDACTED]' "$case_dir/out" 'redacted command output'
    assert_file_eq "$expected_audit" "$case_dir/audit" 'successful audit trail'
    if grep -Eq 'demo-api|demo-pass|demo-token' "$case_dir/out" "$case_dir/audit"; then
        fail 'sensitive successful output was retained'
    fi
    assert_absent "$case_dir/runtime/session-INC-202" 'successful session was not cleaned'
}

test_ticket_scope_blocks_command_text() {
    local case_dir=$TEST_ROOT/scope rc
    mkdir -p "$case_dir/runtime"
    make_ticket "$case_dir/ticket" INC-303 status
    make_consent "$case_dir/consent" INC-303 yes
    printf 'diagnostics; touch escaped\n' >"$case_dir/actions"

    (
        cd "$case_dir" || exit 99
        bash "$SCRIPT" ticket consent actions audit runtime >out 2>err
    )
    rc=$?

    assert_eq 126 "$rc" 'out-of-scope action status'
    assert_absent "$case_dir/escaped" 'action text was executed as shell code'
    if ! grep -Eq '^RESULT ticket=INC-303 .* result=denied rc=126 output=$' "$case_dir/audit"; then
        fail 'out-of-scope action was not audited as denied'
    fi
    if ! grep -Eq '^STOP ticket=INC-303 reason=failed rc=126$' "$case_dir/audit"; then
        fail 'denied session has no failed STOP record'
    fi
    assert_absent "$case_dir/runtime/session-INC-303" 'denied session was not cleaned'
}

test_failed_action_finalizes() {
    local case_dir=$TEST_ROOT/failure rc
    mkdir -p "$case_dir/runtime"
    make_ticket "$case_dir/ticket" INC-404 fail
    make_consent "$case_dir/consent" INC-404 yes
    printf 'fail\n' >"$case_dir/actions"

    bash "$SCRIPT" "$case_dir/ticket" "$case_dir/consent" "$case_dir/actions" \
        "$case_dir/audit" "$case_dir/runtime" >"$case_dir/out" 2>"$case_dir/err"
    rc=$?

    assert_eq 23 "$rc" 'failed action status'
    assert_file_eq 'diagnostic failed secret=[REDACTED]' "$case_dir/out" 'failed output redaction'
    if grep -Eq 'internal-detail' "$case_dir/out" "$case_dir/audit"; then
        fail 'sensitive failed output was retained'
    fi
    if ! grep -Eq '^STOP ticket=INC-404 reason=failed rc=23$' "$case_dir/audit"; then
        fail 'failed session has no STOP record'
    fi
    assert_absent "$case_dir/runtime/session-INC-404" 'failed session was not cleaned'
}

test_disconnect_finalizes() {
    local case_dir=$TEST_ROOT/disconnect pid rc ready=0 attempt
    mkdir -p "$case_dir/runtime"
    make_ticket "$case_dir/ticket" INC-505 hold
    make_consent "$case_dir/consent" INC-505 yes
    printf 'hold\n' >"$case_dir/actions"

    bash "$SCRIPT" "$case_dir/ticket" "$case_dir/consent" "$case_dir/actions" \
        "$case_dir/audit" "$case_dir/runtime" >"$case_dir/out" 2>"$case_dir/err" &
    pid=$!
    for attempt in {1..200}; do
        if [[ -e $case_dir/runtime/session-INC-505/ready ]]; then
            ready=1
            break
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 0.01
    done
    if (( ! ready )); then
        fail 'hold action did not become ready'
        kill -TERM "$pid" 2>/dev/null || true
    else
        kill -TERM "$pid"
    fi
    wait "$pid"
    rc=$?

    assert_eq 143 "$rc" 'disconnect status'
    if ! grep -Eq '^ACTION ticket=INC-505 command=hold$' "$case_dir/audit"; then
        fail 'disconnecting action was not recorded'
    fi
    if ! grep -Eq '^STOP ticket=INC-505 reason=disconnected rc=143$' "$case_dir/audit"; then
        fail 'disconnected session has no STOP record'
    fi
    assert_absent "$case_dir/runtime/session-INC-505" 'disconnected session was not cleaned'
}

test_consent_is_explicit
test_success_and_redaction
test_ticket_scope_blocks_command_text
test_failed_action_finalizes
test_disconnect_finalizes

if (( FAILURES > 0 )); then
    printf '%d test assertion(s) failed\n' "$FAILURES" >&2
    exit 1
fi
printf '%s\n' 'all remote support tests passed'
