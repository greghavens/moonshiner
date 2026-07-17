#!/usr/bin/env bash
# Regression harness for nightly_report.sh (receiving-scan summary).
# Run from the workspace root:  bash test_lostcount.sh
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

if [[ ! -f nightly_report.sh ]]; then
  printf 'FAIL nightly_report.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

nl=$'\n'

# ---- fixtures ---------------------------------------------------------------

# A busy night: two depots, one unparsable scan line, plenty of non-scan noise.
{
  printf '2026-07-12T22:00:00 heartbeat ok\n'
  printf '2026-07-12T22:04:11 scan depot=east sku=AA-1042 qty=3\n'
  printf '2026-07-12T22:09:40 scan depot=west sku=BB-77 qty=12\n'
  printf '2026-07-12T22:11:02 note rescan requested by shift lead\n'
  printf '2026-07-12T22:15:33 scan depot=east sku=AA-9 qty=1\n'
  printf '2026-07-12T22:26:48 error scanner-7 offline\n'
  printf '2026-07-12T23:59:59 scan depot=west sku=CC-9 qty=oops\n'
  printf '2026-07-13T00:02:41 scan depot=east sku=DD-3 qty=5\n'
  printf '2026-07-13T00:07:19 scan depot=west sku=BB-77 qty=2\n'
  printf '2026-07-13T00:10:00 heartbeat ok\n'
} > "$T/night1.log"

# A single-depot night with no surprises.
{
  printf '2026-07-11T22:01:00 scan depot=north sku=EE-4 qty=7\n'
  printf '2026-07-11T22:02:00 scan depot=north sku=EE-5 qty=1\n'
  printf '2026-07-11T22:03:00 scan depot=north sku=EE-4 qty=2\n'
} > "$T/night2.log"

# A quiet night: nothing but heartbeats.
{
  printf '2026-07-10T22:00:00 heartbeat ok\n'
  printf '2026-07-10T23:00:00 heartbeat ok\n'
} > "$T/night3.log"

# ---- argument handling --------------------------------------------------------

run_in "$T" bash "$ROOT/nightly_report.sh"
expect "no arguments" 64 "" "usage: nightly_report.sh <scan-log>$nl"

run_in "$T" bash "$ROOT/nightly_report.sh" absent.log
expect "unreadable log" 66 "" "nightly_report.sh: cannot read: absent.log$nl"

# ---- busy night: totals and per-depot counts must match the handled lines ------

printf -v exp1_out 'handled AA-1042 x3\nhandled BB-77 x12\nhandled AA-9 x1\nhandled DD-3 x5\nhandled BB-77 x2\n-- nightly totals --\nscans: 5\nitems: 23\ndepots:\n  east: 9\n  west: 14\n'
printf -v exp1_err 'nightly_report.sh: skipped unparsable scan: 2026-07-12T23:59:59 scan depot=west sku=CC-9 qty=oops\n'

run_in "$T" bash "$ROOT/nightly_report.sh" night1.log
expect "busy night" 0 "$exp1_out" "$exp1_err"

run_in "$T" bash "$ROOT/nightly_report.sh" night1.log
expect "busy night, second run is identical" 0 "$exp1_out" "$exp1_err"

# ---- single-depot night ----------------------------------------------------------

printf -v exp2_out 'handled EE-4 x7\nhandled EE-5 x1\nhandled EE-4 x2\n-- nightly totals --\nscans: 3\nitems: 10\ndepots:\n  north: 10\n'

run_in "$T" bash "$ROOT/nightly_report.sh" night2.log
expect "single-depot night" 0 "$exp2_out" ""

# ---- quiet night ------------------------------------------------------------------

printf -v exp3_out -- '-- nightly totals --\nscans: 0\nitems: 0\ndepots:\n'

run_in "$T" bash "$ROOT/nightly_report.sh" night3.log
expect "quiet night" 0 "$exp3_out" ""

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
