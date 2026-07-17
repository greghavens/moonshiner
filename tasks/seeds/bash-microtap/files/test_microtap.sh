#!/usr/bin/env bash
# Acceptance harness for microtap.sh (sourceable pure-bash test framework).
# The framework is exercised against sample suites written by this harness;
# the checks here use plain comparisons on purpose — never the framework.
# Run from the workspace root:  bash test_microtap.sh
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- "${0%/*}"

ROOT=$PWD
T=_t
rm -rf "$T"
mkdir -p "$T"
cleanup() { rm -rf "$ROOT/$T"; }
trap cleanup EXIT

checks=0
fails=0

assert_harness_eq() { # assert_harness_eq <label> <expected> <actual>
  checks=$((checks + 1))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

slurp() { # slurp <var> <file> -- byte-exact file contents into var
  IFS= read -r -d '' "$1" < "$2" || true
}

RC=0
OUT=''
ERR=''
run_in() { # run_in <dir> <cmd...> -- capture RC, OUT, ERR byte-exactly
  local d=$1
  shift
  ( cd "$d" && exec "$@" ) > "$ROOT/$T/out" 2> "$ROOT/$T/err"
  RC=$?
  slurp OUT "$ROOT/$T/out"
  slurp ERR "$ROOT/$T/err"
}

if [[ ! -f microtap.sh ]]; then
  printf 'FAIL microtap.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_harness_eq "$1: exit code" "$2" "$RC"
  assert_harness_eq "$1: stdout" "$3" "$OUT"
  assert_harness_eq "$1: stderr" "$4" "$ERR"
}

nl=$'\n'

# ---- sourcing alone is silent and defines everything -------------------------

run_in "$T" env MT_PATH="$ROOT/microtap.sh" bash -c '. "$MT_PATH"'
expect "sourcing produces no output" 0 "" ""

run_in "$T" env MT_PATH="$ROOT/microtap.sh" bash -c \
  '. "$MT_PATH"; for f in mt_test mt_run assert_eq assert_ne assert_fail assert_out; do declare -F "$f" > /dev/null || { echo "missing $f"; exit 1; }; done; echo defined'
expect "sourcing defines the whole API" 0 "defined$nl" ""

# ---- suite 1: everything passes -----------------------------------------------

cat > "$T/suite_pass.sh" <<'SUITE'
#!/usr/bin/env bash
. "$MT_PATH"

test_strings() {
  assert_eq "apple" "apple"
  assert_ne "apple" "pear"
}
test_output() {
  assert_out "hello world" echo hello world
  assert_out "" bash -c 'exit 5'
}
test_failing_cmd() {
  assert_fail bash -c 'exit 3'
}

mt_test "strings compare" test_strings
mt_test "command output captured" test_output
mt_test "failing command detected" test_failing_cmd
mt_run
SUITE

printf -v exp_pass '1..3\nok 1 - strings compare\nok 2 - command output captured\nok 3 - failing command detected\n# pass 3 fail 0\n'
run_in "$T" env MT_PATH="$ROOT/microtap.sh" bash suite_pass.sh
expect "all-pass suite" 0 "$exp_pass" ""

# ---- suite 2: one test fails, diagnostics land before its result line -----------

cat > "$T/suite_fail.sh" <<'SUITE'
#!/usr/bin/env bash
. "$MT_PATH"

test_adds() {
  assert_eq 4 $((2 + 2))
}
test_multiplies() {
  assert_eq 6 $((2 * 4))
  assert_out sum echo total
  assert_eq fine fine
}
test_still_runs() {
  assert_ne borked "healthy"
  assert_fail false
}

mt_test "adds" test_adds
mt_test "multiplies" test_multiplies
mt_test "later tests still run" test_still_runs
mt_run
status=$?
printf 'suite status %d\n' "$status"
exit "$status"
SUITE

printf -v exp_fail "1..3\nok 1 - adds\n# assert_eq failed: expected '6' actual '8'\n# assert_out failed: expected 'sum' actual 'total'\nnot ok 2 - multiplies\nok 3 - later tests still run\n# pass 2 fail 1\nsuite status 1\n"
run_in "$T" env MT_PATH="$ROOT/microtap.sh" bash suite_fail.sh
expect "one-fail suite" 1 "$exp_fail" ""

# ---- suite 3: setup/teardown wrap every test, teardown runs on failure too ------

cat > "$T/suite_hooks.sh" <<'SUITE'
#!/usr/bin/env bash
. "$MT_PATH"

setup() {
  FIXTURE_STATE=ready
  printf 'setup\n' >> hooks.log
}
teardown() {
  printf 'teardown\n' >> hooks.log
}

test_first() {
  printf 'run:first\n' >> hooks.log
  assert_eq ready "$FIXTURE_STATE"
}
test_second() {
  printf 'run:second\n' >> hooks.log
  assert_eq want made
}
test_third() {
  printf 'run:third\n' >> hooks.log
  assert_fail false
}

mt_test "first" test_first
mt_test "second" test_second
mt_test "third" test_third
mt_run
SUITE

rm -f "$T/hooks.log"
printf -v exp_hooks "1..3\nok 1 - first\n# assert_eq failed: expected 'want' actual 'made'\nnot ok 2 - second\nok 3 - third\n# pass 2 fail 1\n"
run_in "$T" env MT_PATH="$ROOT/microtap.sh" bash suite_hooks.sh
expect "hooks suite" 1 "$exp_hooks" ""

LOG=''
slurp LOG "$T/hooks.log"
printf -v exp_log 'setup\nrun:first\nteardown\nsetup\nrun:second\nteardown\nsetup\nrun:third\nteardown\n'
assert_harness_eq "hooks: setup/teardown wrap every test, failure included" "$exp_log" "$LOG"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
