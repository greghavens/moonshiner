#!/usr/bin/env bash
# Acceptance harness for retry.sh.
# Run from the workspace root:  bash test_retry.sh
# Zero real sleeping: every case injects RETRY_SLEEP_CMD.
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

assert_eq() { # assert_eq <label> <expected> <actual>
  checks=$((checks + 1))
  if [[ "$2" == "$3" ]]; then
    printf 'PASS %s\n' "$1"
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
  return 1
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

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

sleeplog() { # sleeplog <expected> -- recorded fake-sleep delays, one per line
  local got=''
  if [[ -f "$T/sleep.log" ]]; then
    slurp got "$T/sleep.log"
  fi
  assert_eq "recorded backoff delays" "$1" "$got"
}

if [[ ! -f retry.sh ]]; then
  printf 'FAIL retry.sh not found in the workspace root\n'
  exit 1
fi

# ---- fixtures: a scripted flaky command and a logging fake sleeper -----------------

cat > "$T/flaky.sh" <<'EOF'
#!/usr/bin/env bash
# flaky.sh <plan-file> [args...] -- exit code for run N is line N of the plan
# (0 when the plan runs out). Prints one stdout marker per run; with
# FLAKY_ERR set, also one stderr marker.
plan=$1
shift
n=0
[ -f "$plan.n" ] && n=$(cat "$plan.n")
n=$((n + 1))
printf '%s' "$n" > "$plan.n"
code=$(awk -v n="$n" 'NR == n { print; found = 1 } END { if (!found) print 0 }' "$plan")
printf 'run %d' "$n"
for a in "$@"; do
  printf '[%s]' "$a"
done
printf '\n'
if [ -n "${FLAKY_ERR:-}" ]; then
  printf 'err %d\n' "$n" >&2
fi
exit "$code"
EOF

cat > "$T/fakesleep.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$1" >> sleep.log
EOF

cat > "$T/badsleep.sh" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF

plan() { # plan <code>... -- write the flaky plan, reset counters and sleep log
  rm -f "$T/plan" "$T/plan.n" "$T/sleep.log"
  printf '%s\n' "$@" > "$T/plan"
}

RETRY() { # RETRY <argv...> -- run retry.sh in $T with the fake sleeper injected
  run_in "$T" env RETRY_SLEEP_CMD='bash fakesleep.sh' bash "$ROOT/retry.sh" "$@"
}

# ---- immediate success --------------------------------------------------------------

plan 0
RETRY -- bash flaky.sh plan push cache
printf -v exp_err 'retry: attempt 1/4: ok\n'
expect "first-try success" 0 'run 1[push][cache]'$'\n' "$exp_err"
sleeplog ""

# ---- two retryable failures, then success; args and stderr pass through -------------

plan 75 75 0
run_in "$T" env RETRY_SLEEP_CMD='bash fakesleep.sh' FLAKY_ERR=1 \
  bash "$ROOT/retry.sh" -- bash flaky.sh plan '' 'two words'
printf -v exp_out 'run 1[][two words]\nrun 2[][two words]\nrun 3[][two words]\n'
printf -v exp_err 'err 1\nretry: attempt 1/4: exit 75 (retryable), sleeping 1s\nerr 2\nretry: attempt 2/4: exit 75 (retryable), sleeping 3s\nerr 3\nretry: attempt 3/4: ok\n'
expect "retry until success" 0 "$exp_out" "$exp_err"
sleeplog $'1\n3\n'

# ---- exhaustion: retryable to the end, exit is the last attempt's code ---------------

plan 1 1 1
RETRY --max 3 -- bash flaky.sh plan
printf -v exp_out 'run 1\nrun 2\nrun 3\n'
printf -v exp_err 'retry: attempt 1/3: exit 1 (retryable), sleeping 1s\nretry: attempt 2/3: exit 1 (retryable), sleeping 3s\nretry: attempt 3/3: exit 1 (retryable), giving up\n'
expect "attempts exhausted" 1 "$exp_out" "$exp_err"
sleeplog $'1\n3\n'

# ---- listed-fatal code stops on the spot ----------------------------------------------

plan 78 0
RETRY -- bash flaky.sh plan
printf -v exp_err 'retry: attempt 1/4: exit 78 (fatal), giving up\n'
expect "fatal code stops immediately" 78 'run 1'$'\n' "$exp_err"
sleeplog ""
assert_eq "no second attempt after a fatal code" "1" "$(cat "$T/plan.n")"

# ---- unlisted code is fatal too --------------------------------------------------------

plan 9 0
RETRY -- bash flaky.sh plan
printf -v exp_err 'retry: attempt 1/4: exit 9 (fatal), giving up\n'
expect "unlisted code treated as fatal" 9 'run 1'$'\n' "$exp_err"
sleeplog ""

# ---- custom code classes ----------------------------------------------------------------

plan 9 17 3
RETRY --retry-on 9,17 --fatal-on 3 -- bash flaky.sh plan
printf -v exp_out 'run 1\nrun 2\nrun 3\n'
printf -v exp_err 'retry: attempt 1/4: exit 9 (retryable), sleeping 1s\nretry: attempt 2/4: exit 17 (retryable), sleeping 3s\nretry: attempt 3/4: exit 3 (fatal), giving up\n'
expect "custom retry/fatal lists" 3 "$exp_out" "$exp_err"
sleeplog $'1\n3\n'

# ---- full backoff table: 1, 3, 7, 15, then 30 --------------------------------------------

plan 75 75 75 75 75 75
RETRY --max 6 -- bash flaky.sh plan
printf -v exp_out 'run 1\nrun 2\nrun 3\nrun 4\nrun 5\nrun 6\n'
printf -v exp_err 'retry: attempt 1/6: exit 75 (retryable), sleeping 1s\nretry: attempt 2/6: exit 75 (retryable), sleeping 3s\nretry: attempt 3/6: exit 75 (retryable), sleeping 7s\nretry: attempt 4/6: exit 75 (retryable), sleeping 15s\nretry: attempt 5/6: exit 75 (retryable), sleeping 30s\nretry: attempt 6/6: exit 75 (retryable), giving up\n'
expect "backoff schedule table" 75 "$exp_out" "$exp_err"
sleeplog $'1\n3\n7\n15\n30\n'

# ---- literal flag-looking args after -- pass through untouched -----------------------------

plan 0
RETRY -- bash flaky.sh plan --max -x
expect "args after -- are never parsed" 0 'run 1[--max][-x]'$'\n' 'retry: attempt 1/4: ok'$'\n'

# ---- a broken sleep command aborts the run --------------------------------------------------

plan 75 0
run_in "$T" env RETRY_SLEEP_CMD='bash badsleep.sh' bash "$ROOT/retry.sh" -- bash flaky.sh plan
printf -v exp_err 'retry: attempt 1/4: exit 75 (retryable), sleeping 1s\nretry: sleep command failed\n'
expect "failing sleep command aborts" 70 'run 1'$'\n' "$exp_err"
assert_eq "no attempt after the failed sleep" "1" "$(cat "$T/plan.n")"

# ---- usage errors ----------------------------------------------------------------------------

printf -v exp_usage 'usage: retry.sh [--max N] [--retry-on codes] [--fatal-on codes] -- cmd [args...]\n'

usage_case() { # usage_case <label> <argv...>
  local label=$1
  shift
  plan 0
  RETRY "$@"
  expect "$label" 64 "" "$exp_usage"
}

usage_case "no arguments at all"
usage_case "missing the -- separator" bash flaky.sh plan
usage_case "flags but no --" --max 3 bash flaky.sh plan
usage_case "nothing after --" --
usage_case "--max zero" --max 0 -- bash flaky.sh plan
usage_case "--max non-numeric" --max lots -- bash flaky.sh plan
usage_case "--max missing its value" --max
usage_case "empty retry list" --retry-on '' -- bash flaky.sh plan
usage_case "blank item in a code list" --retry-on '1,,2' -- bash flaky.sh plan
usage_case "non-numeric code in a list" --fatal-on '3,x' -- bash flaky.sh plan
usage_case "zero in a code list" --retry-on '0,1' -- bash flaky.sh plan
usage_case "code in both lists" --retry-on 5 --fatal-on 5 -- bash flaky.sh plan
usage_case "unknown flag" --jitter -- bash flaky.sh plan

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf 'SUMMARY: %d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'SUMMARY: all %d checks passed\n' "$checks"
