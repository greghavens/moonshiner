#!/usr/bin/env bash
# Acceptance harness for withlock.sh (flock-guarded command runner).
# Run from the workspace root:  bash test_flockq.sh
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
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

assert_nonempty_file() { # assert_nonempty_file <label> <path>
  checks=$((checks + 1))
  if [[ -s "$2" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (missing or empty: %s)\n' "$1" "$2"
}

assert_empty_file() { # assert_empty_file <label> <path> -- exists and 0 bytes
  checks=$((checks + 1))
  if [[ -f "$2" && ! -s "$2" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (expected empty file: %s)\n' "$1" "$2"
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

# wait (bounded, ~10s) for a path to appear; FAIL the case if it never does
wait_for() { # wait_for <label> <path>
  local n=0
  while [[ ! -e "$2" && $n -lt 200 ]]; do
    sleep 0.05
    n=$((n + 1))
  done
  checks=$((checks + 1))
  if [[ -e "$2" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (timed out waiting for %s)\n' "$1" "$2"
  return 1
}

if [[ ! -f withlock.sh ]]; then
  printf 'FAIL withlock.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

nl=$'\n'

# ---- argument validation -------------------------------------------------------

printf -v exp_usage 'usage: withlock.sh <lockfile> <command> [args...]\n'
run_in "$T" bash "$ROOT/withlock.sh"
expect "no arguments" 64 "" "$exp_usage"
run_in "$T" bash "$ROOT/withlock.sh" lock.f
expect "lockfile but no command" 64 "" "$exp_usage"

# ---- plain run: lock created, command exit code passes through -------------------

run_in "$T" bash "$ROOT/withlock.sh" lock5.f printf '%s|%s\n' "two words" x
expect "plain run forwards command words intact" 0 "two words|x$nl" ""
assert_empty_file "plain run: lock released (empty)" "$T/lock5.f"

run_in "$T" bash "$ROOT/withlock.sh" lock4.f bash -c 'exit 7'
expect "failing command: exit code passes through" 7 "" ""
assert_empty_file "failing command: lock still released" "$T/lock4.f"

run_in "$T" bash "$ROOT/withlock.sh" lock4.f echo ok
expect "reacquire after a failed command" 0 "ok$nl" ""

# ---- contention: holder keeps the lock, waiters queue up --------------------------

cat > "$T/hold_job.sh" <<'HOLD'
#!/usr/bin/env bash
: > a_started
n=0
while [[ ! -e release_a && $n -lt 400 ]]; do
  sleep 0.05
  n=$((n + 1))
done
printf 'holder finished\n'
exit 0
HOLD

rm -f "$T/a_started" "$T/release_a"
( cd "$T" && exec bash "$ROOT/withlock.sh" lock.f bash hold_job.sh ) \
    > "$ROOT/$T/a.out" 2> "$ROOT/$T/a.err" &
apid=$!

if wait_for "contention: holder is running" "$T/a_started"; then
  assert_nonempty_file "contention: lockfile records the holder while held" "$T/lock.f"

  run_in "$T" bash "$ROOT/withlock.sh" lock.f echo second
  expect "contention: second caller is turned away" 75 "" "withlock.sh: busy: lock.f$nl"

  run_in "$T" bash "$ROOT/withlock.sh" lock.f echo again
  expect "contention: third caller is turned away" 75 "" "withlock.sh: busy: lock.f$nl"

  Q=''
  slurp Q "$T/lock.f.queue"
  assert_eq "contention: queue records both waiters in order" "echo second${nl}echo again$nl" "$Q"
fi

: > "$T/release_a"
wait "$apid"
RC=$?
slurp OUT "$ROOT/$T/a.out"
slurp ERR "$ROOT/$T/a.err"
expect "contention: holder finishes cleanly" 0 "holder finished$nl" ""
assert_empty_file "contention: lock released after holder" "$T/lock.f"

run_in "$T" bash "$ROOT/withlock.sh" lock.f echo second
expect "contention: retry succeeds once the lock is free" 0 "second$nl" ""
assert_empty_file "contention: lock released after retry" "$T/lock.f"
Q=''
slurp Q "$T/lock.f.queue"
assert_eq "contention: queue is append-only history" "echo second${nl}echo again$nl" "$Q"

# ---- leftover lock from an interrupted run: recorded process is gone --------------

bash -c 'exit 0' &
gone_pid=$!
wait "$gone_pid" 2>/dev/null || true
printf '%s\n' "$gone_pid" > "$T/lock2.f"

run_in "$T" bash "$ROOT/withlock.sh" lock2.f echo took
expect "leftover lock: cleared and taken" 0 "took$nl" "withlock.sh: clearing leftover lock: lock2.f$nl"
assert_empty_file "leftover lock: released after run" "$T/lock2.f"

# ---- recorded process still alive (legacy holder without flock): stay away --------

printf '%s\n' "$$" > "$T/lock3.f"
BEFORE=''
slurp BEFORE "$T/lock3.f"

run_in "$T" bash "$ROOT/withlock.sh" lock3.f echo third
expect "live holder on record: turned away" 75 "" "withlock.sh: busy: lock3.f$nl"
AFTER=''
slurp AFTER "$T/lock3.f"
assert_eq "live holder on record: lockfile untouched" "$BEFORE" "$AFTER"
Q=''
slurp Q "$T/lock3.f.queue"
assert_eq "live holder on record: waiter queued" "echo third$nl" "$Q"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
