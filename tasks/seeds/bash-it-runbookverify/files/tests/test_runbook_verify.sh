#!/usr/bin/env bash

set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
verifier=$repo_root/runbook-verify
test_tmp=$(mktemp -d "${TMPDIR:-/tmp}/test-runbook-verify.XXXXXX") || exit 1

cleanup() {
    rm -rf -- "$test_tmp"
}
trap cleanup EXIT

failures=0

fail() {
    printf 'not ok - %s\n' "$1" >&2
    ((failures += 1))
}

assert_empty() {
    local file=$1
    local label=$2
    [[ ! -s $file ]] || fail "$label"
}

assert_file_eq() {
    local expected=$1
    local actual=$2
    local label=$3
    cmp -s -- "$expected" "$actual" || fail "$label"
}

if ! cmp_version_output=$(LC_ALL=C cmp --version 2>/dev/null); then
    printf 'Bail out! cmp --version is unavailable\n' >&2
    exit 1
fi
cmp_version=${cmp_version_output%%$'\n'*}

# The bundled runbook proves safe blocks run in declaration order while the
# mutation fence and an ordinary shell fence remain inert.
happy_root=$test_tmp/happy
scratch_root=$happy_root/scratch
mkdir -p -- "$scratch_root"
happy_stdout=$happy_root/stdout
happy_stderr=$happy_root/stderr
happy_report=$happy_root/report
mutation_canary=$happy_root/mutation-ran

(
    cd -- "$repo_root" || exit 125
    TMPDIR=$scratch_root MUTATION_CANARY=$mutation_canary \
        bash "$verifier" --fixtures fixtures --report "$happy_report" \
        runbooks/service-recovery.md
) > "$happy_stdout" 2> "$happy_stderr"
happy_status=$?

(( happy_status == 0 )) || fail "bundled verification should pass"
assert_empty "$happy_stdout" "successful verification wrote stdout"
assert_empty "$happy_stderr" "successful verification wrote stderr"
[[ ! -e $mutation_canary ]] || fail "mutation verification block executed"

happy_expected=$happy_root/expected-report
printf '%s\n' \
    'FORMAT runbook-verify-report-v1' \
    "TOOL bash $BASH_VERSION" \
    "TOOL cmp $cmp_version" \
    'PASS runbooks/service-recovery.md:status' \
    'PASS runbooks/service-recovery.md:plan' \
    'SKIP runbooks/service-recovery.md:restart-production safety=mutation' \
    'SUMMARY pass=2 fail=0 skip=1' > "$happy_expected"
assert_file_eq "$happy_expected" "$happy_report" \
    "bundled report was not reproducible"

if [[ -n $(find "$scratch_root" -mindepth 1 -print -quit) ]]; then
    fail "harness scratch directory leaked after success"
fi

# Build a local fixture whose actual output differs from expected only by one
# trailing newline. Byte-exact comparison must reject it.
newline_root=$test_tmp/newline-case
mkdir -p -- "$newline_root/fixtures/newlines/bin" \
    "$newline_root/fixtures/newlines/expected" "$newline_root/runbooks" \
    "$newline_root/scratch"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    "printf 'ready\\n'" > "$newline_root/fixtures/newlines/bin/check.sh"
printf 'ready\n\n' > "$newline_root/fixtures/newlines/expected/check.out"
printf '%s\n' \
    '# Newline check' \
    '' \
    '```verify id=trailing-newline safety=safe fixture=newlines expected=expected/check.out' \
    'bash bin/check.sh' \
    '```' > "$newline_root/runbooks/newlines.md"

newline_stdout=$newline_root/stdout
newline_stderr=$newline_root/stderr
(
    cd -- "$newline_root" || exit 125
    TMPDIR=$newline_root/scratch bash "$verifier" \
        --fixtures fixtures --report report.txt runbooks/newlines.md
) > "$newline_stdout" 2> "$newline_stderr"
newline_status=$?

(( newline_status == 1 )) || fail "trailing-newline mismatch should fail"
assert_empty "$newline_stdout" "newline mismatch wrote stdout"
assert_empty "$newline_stderr" "newline mismatch wrote stderr"

newline_expected=$newline_root/expected-report
printf '%s\n' \
    'FORMAT runbook-verify-report-v1' \
    "TOOL bash $BASH_VERSION" \
    "TOOL cmp $cmp_version" \
    'FAIL runbooks/newlines.md:trailing-newline output' \
    'SUMMARY pass=0 fail=1 skip=0' > "$newline_expected"
assert_file_eq "$newline_expected" "$newline_root/report.txt" \
    "newline mismatch report was incorrect"
if [[ -n $(find "$newline_root/scratch" -mindepth 1 -print -quit) ]]; then
    fail "harness scratch directory leaked after output failure"
fi

# Failure precedence is deterministic: an exit status is reported ahead of
# stderr, and successful commands with stderr are reported as stderr failures.
failure_root=$test_tmp/failure-case
mkdir -p -- "$failure_root/fixtures/failures/expected" \
    "$failure_root/runbooks" "$failure_root/scratch"
printf 'unused\n' > "$failure_root/fixtures/failures/expected/unused.out"
printf '%s\n' \
    '# Failure reporting' \
    '' \
    '```verify id=exit-first safety=safe fixture=failures expected=expected/unused.out' \
    "printf 'diagnostic\\n' >&2" \
    'exit 7' \
    '```' \
    '' \
    '```verify id=stderr-only safety=safe fixture=failures expected=expected/unused.out' \
    "printf 'unused\\n'" \
    "printf 'warning\\n' >&2" \
    '```' > "$failure_root/runbooks/failures.md"

(
    cd -- "$failure_root" || exit 125
    TMPDIR=$failure_root/scratch bash "$verifier" \
        --fixtures fixtures --report report.txt runbooks/failures.md
) > "$failure_root/stdout" 2> "$failure_root/stderr"
failure_status=$?

(( failure_status == 1 )) || fail "command failures should set status 1"
assert_empty "$failure_root/stdout" "command failure wrote stdout"
assert_empty "$failure_root/stderr" "command failure wrote harness stderr"
failure_expected=$failure_root/expected-report
printf '%s\n' \
    'FORMAT runbook-verify-report-v1' \
    "TOOL bash $BASH_VERSION" \
    "TOOL cmp $cmp_version" \
    'FAIL runbooks/failures.md:exit-first exit=7' \
    'FAIL runbooks/failures.md:stderr-only stderr' \
    'SUMMARY pass=0 fail=2 skip=0' > "$failure_expected"
assert_file_eq "$failure_expected" "$failure_root/report.txt" \
    "command failure precedence was incorrect"
if [[ -n $(find "$failure_root/scratch" -mindepth 1 -print -quit) ]]; then
    fail "harness scratch directory leaked after command failure"
fi

# Fixture commands run against a copy. A safe command may write in that copy,
# but the original fixture must remain unchanged afterward.
copy_root=$test_tmp/copy-case
mkdir -p -- "$copy_root/fixtures/copy/state" \
    "$copy_root/fixtures/copy/expected" "$copy_root/runbooks"
printf 'original\n' > "$copy_root/fixtures/copy/state/value"
printf 'original\n' > "$copy_root/fixtures/copy/expected/value.out"
printf '%s\n' \
    '# Copy isolation' \
    '' \
    '```verify id=copy safety=safe fixture=copy expected=expected/value.out' \
    'IFS= read -r value < state/value' \
    "printf '%s\\n' \"\$value\"" \
    "printf 'changed\\n' > state/value" \
    '```' > "$copy_root/runbooks/copy.md"

(
    cd -- "$copy_root" || exit 125
    bash "$verifier" --fixtures fixtures --report report.txt runbooks/copy.md
) > "$copy_root/stdout" 2> "$copy_root/stderr"
copy_status=$?
(( copy_status == 0 )) || fail "copy-isolation verification should pass"
[[ $(<"$copy_root/fixtures/copy/state/value") == original ]] || \
    fail "safe verification changed the original fixture"

if (( failures > 0 )); then
    printf '1..1\nnot ok 1 - runbook-verify (%d assertions failed)\n' "$failures"
    exit 1
fi

printf '1..1\nok 1 - runbook-verify\n'
