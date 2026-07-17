#!/usr/bin/env bash
# Regression harness for tray_audit.sh (propagation-house walk-through summary).
# Run from the workspace root:  bash test_trayaudit.sh
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

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

nl=$'\n'

if [[ ! -f tray_audit.sh ]]; then
  printf 'FAIL tray_audit.sh not found in the workspace root\n'
  exit 1
fi

# ---- the script must parse: bash -n is part of the gate ----------------------

bash -n tray_audit.sh > "$T/nout" 2> "$T/nerr"
nrc=$?
slurp NERR "$T/nerr"
assert_eq "bash -n tray_audit.sh: exit code" 0 "$nrc"
assert_eq "bash -n tray_audit.sh: stderr" "" "$NERR"

# ---- fixtures -----------------------------------------------------------------

{
  printf '# monday walk, house 2\n'
  printf 'T-101 plug 25\n'
  printf 'T-102 flat 8\n'
  printf 'T-103 cell 21\n'
  printf 'T-104 plug 3\n'
  printf '\n'
  printf 'T-105 flat 30\n'
  printf 'T-106 tape 12\n'
  printf 'T-107 plug 21\n'
} > "$T/monday.txt"

{
  printf '# quiet bench, all fresh\n'
  printf 'Q-1 plug 2\n'
  printf 'Q-2 flat 20\n'
  printf 'Q-3 cell 0\n'
} > "$T/fresh.txt"

{
  printf '# nothing sown yet\n'
  printf '\n'
} > "$T/empty.txt"

# ---- argument handling ----------------------------------------------------------

run_in "$T" bash "$ROOT/tray_audit.sh"
expect "no arguments" 64 "" "usage: tray_audit.sh <manifest>$nl"

run_in "$T" bash "$ROOT/tray_audit.sh" a.txt b.txt
expect "too many arguments" 64 "" "usage: tray_audit.sh <manifest>$nl"

run_in "$T" bash "$ROOT/tray_audit.sh" absent.txt
expect "unreadable manifest" 66 "" "tray_audit.sh: cannot read: absent.txt$nl"

# ---- busy manifest: overdue lines in file order, then the tallies ----------------

printf -v exp1_out '%s\n' \
  'overdue: T-101 (plug tray, day 25) - pot on or toss' \
  'overdue: T-103 (cell, day 21)' \
  'overdue: T-105 (flat, day 30)' \
  'overdue: T-107 (plug tray, day 21) - pot on or toss' \
  'trays checked: 7' \
  'overdue: 4' \
  'by kind: plug=3 flat=2 cell=1 other=1'

run_in "$T" bash "$ROOT/tray_audit.sh" monday.txt
expect "monday walk-through" 0 "$exp1_out" ""

run_in "$T" bash "$ROOT/tray_audit.sh" monday.txt
expect "monday walk-through, second run identical" 0 "$exp1_out" ""

# ---- fresh bench: day 20 is not overdue yet --------------------------------------

printf -v exp2_out '%s\n' \
  'trays checked: 3' \
  'overdue: 0' \
  'by kind: plug=1 flat=1 cell=1 other=0'

run_in "$T" bash "$ROOT/tray_audit.sh" fresh.txt
expect "fresh bench" 0 "$exp2_out" ""

# ---- comments and blank lines only ------------------------------------------------

printf -v exp3_out '%s\n' \
  'trays checked: 0' \
  'overdue: 0' \
  'by kind: plug=0 flat=0 cell=0 other=0'

run_in "$T" bash "$ROOT/tray_audit.sh" empty.txt
expect "empty manifest" 0 "$exp3_out" ""

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
