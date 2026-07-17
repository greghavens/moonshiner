#!/usr/bin/env bash
# Acceptance harness for logtop.sh.
# Run from the workspace root:  bash test_logtop.sh
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

if [[ ! -f logtop.sh ]]; then
  printf 'FAIL logtop.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

# ---- fixtures ---------------------------------------------------------------

# Edge feed: ts method path status bytes, single spaces, logical timestamps.
printf '%s\n' \
  '100 GET /api/users 200 512' \
  '105 GET /api/users 200 233' \
  '110 POST /api/orders 201 90' \
  '115 GET /static/app.js 304 0' \
  '120 GET /api/users 500 44' \
  '125 GET /api/orders 200 120' \
  '130 GET /missing 404 12' \
  '135 GET /api/orders 200 133' \
  '140 POST /api/orders 200 77' \
  '145 GET /static/app.js 200 5120' \
  '150 GET /health 200 2' \
  '155 GET /health 200 2' \
  > "$T/access.log"

# Tie feed: every path ends up with the same hit count.
printf '%s\n' \
  '10 GET /zeta 200 1' \
  '10 GET /alpha 200 1' \
  '11 GET /mid 200 1' \
  '12 GET /zeta 200 1' \
  '12 GET /alpha 200 1' \
  '13 GET /mid 200 1' \
  > "$T/ties.log"

# ---- full feed, default --top 3 ----------------------------------------------

printf -v exp_default 'CLASSES\n2xx\t9\n3xx\t1\n4xx\t1\n5xx\t1\nTOP PATHS\n/api/orders\t4\n/api/users\t3\n/health\t2\n'

run_in "$T" bash "$ROOT/logtop.sh" access.log
expect "full feed, default top 3" 0 "$exp_default" ""
first_out=$OUT

run_in "$T" bash "$ROOT/logtop.sh" access.log
expect "full feed, second run" 0 "$exp_default" ""
assert_eq "report is byte-stable across runs" "$first_out" "$OUT"

# ---- --top larger than the number of distinct paths ---------------------------

printf -v exp_top10 'CLASSES\n2xx\t9\n3xx\t1\n4xx\t1\n5xx\t1\nTOP PATHS\n/api/orders\t4\n/api/users\t3\n/health\t2\n/static/app.js\t2\n/missing\t1\n'

run_in "$T" bash "$ROOT/logtop.sh" --top 10 access.log
expect "--top 10 lists every path" 0 "$exp_top10" ""

# ---- stable ties: equal hit counts order by path, C byte order ----------------

printf -v exp_ties3 'CLASSES\n2xx\t6\n3xx\t0\n4xx\t0\n5xx\t0\nTOP PATHS\n/alpha\t2\n/mid\t2\n/zeta\t2\n'

run_in "$T" bash "$ROOT/logtop.sh" --top 3 ties.log
expect "all-tied feed, top 3" 0 "$exp_ties3" ""

printf -v exp_ties2 'CLASSES\n2xx\t6\n3xx\t0\n4xx\t0\n5xx\t0\nTOP PATHS\n/alpha\t2\n/mid\t2\n'

run_in "$T" bash "$ROOT/logtop.sh" --top 2 ties.log
expect "truncation inside a tie keeps path order" 0 "$exp_ties2" ""

# ---- time window: both bounds inclusive ---------------------------------------

printf -v exp_window 'CLASSES\n2xx\t3\n3xx\t1\n4xx\t1\n5xx\t1\nTOP PATHS\n/api/orders\t3\n/api/users\t1\n'

run_in "$T" bash "$ROOT/logtop.sh" --from 115 --to 140 --top 2 access.log
expect "window 115..140 inclusive" 0 "$exp_window" ""

printf -v exp_from 'CLASSES\n2xx\t3\n3xx\t0\n4xx\t0\n5xx\t0\nTOP PATHS\n/health\t2\n/static/app.js\t1\n'

run_in "$T" bash "$ROOT/logtop.sh" --from 145 access.log
expect "--from only" 0 "$exp_from" ""

printf -v exp_to 'CLASSES\n2xx\t2\n3xx\t0\n4xx\t0\n5xx\t0\nTOP PATHS\n/api/users\t2\n'

run_in "$T" bash "$ROOT/logtop.sh" --to 105 access.log
expect "--to only" 0 "$exp_to" ""

# flags after the file must also work
run_in "$T" bash "$ROOT/logtop.sh" access.log --to 105
expect "flag placed after the file" 0 "$exp_to" ""

# ---- window that matches nothing ----------------------------------------------

printf -v exp_empty 'CLASSES\n2xx\t0\n3xx\t0\n4xx\t0\n5xx\t0\nTOP PATHS\n'

run_in "$T" bash "$ROOT/logtop.sh" --from 900 access.log
expect "empty window still prints both sections" 0 "$exp_empty" ""

# ---- argument errors -----------------------------------------------------------

printf -v exp_usage 'usage: logtop.sh [--from TS] [--to TS] [--top N] <logfile>\n'

run_in "$T" bash "$ROOT/logtop.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/logtop.sh" access.log ties.log
expect "two logfiles" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/logtop.sh" --top nope access.log
expect "--top with a non-numeric value" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/logtop.sh" --limit 3 access.log
expect "unknown flag" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/logtop.sh" access.log --from
expect "flag missing its value" 64 "" "$exp_usage"

printf -v exp_noread 'logtop.sh: cannot read: nope.log\n'
run_in "$T" bash "$ROOT/logtop.sh" nope.log
expect "unreadable logfile" 66 "" "$exp_noread"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
