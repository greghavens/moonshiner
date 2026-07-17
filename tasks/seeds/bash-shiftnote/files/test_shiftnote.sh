#!/usr/bin/env bash
# Regression harness for handover.sh (end-of-shift report renderer).
# Run from the workspace root:  bash test_shiftnote.sh
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

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
run_handover() { # run_handover <args...>
  ( cd "$ROOT/$T" && exec bash "$ROOT/handover.sh" "$@" ) \
    > "$ROOT/$T/.out" 2> "$ROOT/$T/.err"
  RC=$?
  slurp OUT "$ROOT/$T/.out"
  slurp ERR "$ROOT/$T/.err"
}

if [[ ! -f handover.sh ]]; then
  printf 'FAIL handover.sh not found in the workspace root\n'
  exit 1
fi

nl=$'\n'
tab=$'\t'

# ---- 1. a normal shift: counts, notes, tab-separated rows, real blank lines --
{
  printf '07:14\tOPEN-DB\tcooler alarm reset itself\n'
  printf '07:40\tOPEN-DB\t\n'
  printf '08:15\tOPEN-NET\t\n'
  printf '09:02\tINFO\tvendor called back re: dock 4\n'
} > "$ROOT/$T/events.tsv"

run_handover events.tsv day-12 maria.v report.txt
assert_eq 'normal shift: exit code' '0' "$RC"
assert_eq 'normal shift: stderr is quiet' '' "$ERR"
assert_eq 'normal shift: stdout' "handover written: report.txt${nl}" "$OUT"

expected="SHIFT HANDOVER -- day-12${nl}"
expected+="${nl}"
expected+="OPEN ITEMS${nl}"
expected+="OPEN-DB${tab}2${nl}"
expected+="OPEN-NET${tab}1${nl}"
expected+="${nl}"
expected+="NOTES${nl}"
expected+="- 07:14  cooler alarm reset itself${nl}"
expected+="- 09:02  vendor called back re: dock 4${nl}"
expected+="${nl}"
expected+="Questions: page maria.v via the rota.${nl}"
REPORT=''
slurp REPORT "$ROOT/$T/report.txt"
assert_eq 'normal shift: report is byte-exact' "$expected" "$REPORT"

# the importer splits rows on a real tab: exactly one tab per OPEN ITEMS row
rowcheck=$(grep -c "OPEN-DB${tab}2" "$ROOT/$T/report.txt" || true)
assert_eq 'normal shift: OPEN-DB row is tab-separated' '1' "$rowcheck"
bslash=$(grep -c '\\' "$ROOT/$T/report.txt" || true)
assert_eq 'normal shift: no backslash escapes leak into the report' '0' "$bslash"

# ---- 2. a quiet shift: placeholders, footer still names the on-call ----------
: > "$ROOT/$T/quiet.tsv"
run_handover quiet.tsv night-3 sam.o quiet.txt
assert_eq 'quiet shift: exit code' '0' "$RC"
assert_eq 'quiet shift: stderr is quiet' '' "$ERR"

expected="SHIFT HANDOVER -- night-3${nl}"
expected+="${nl}"
expected+="OPEN ITEMS${nl}"
expected+="(none)${nl}"
expected+="${nl}"
expected+="NOTES${nl}"
expected+="(none)${nl}"
expected+="${nl}"
expected+="Questions: page sam.o via the rota.${nl}"
QUIET=''
slurp QUIET "$ROOT/$T/quiet.txt"
assert_eq 'quiet shift: report is byte-exact' "$expected" "$QUIET"

# ---- 3. usage and missing-file errors ----------------------------------------
run_handover only two args
assert_eq 'bad usage: exit code' '2' "$RC"
assert_eq 'bad usage: stderr' \
  "usage: handover.sh <events.tsv> <shift-label> <oncall> <out.txt>${nl}" "$ERR"

run_handover missing.tsv day-1 kim.r out.txt
assert_eq 'missing events: exit code' '2' "$RC"
assert_eq 'missing events: stderr' \
  "handover: no such events file: missing.tsv${nl}" "$ERR"

# ---- summary -----------------------------------------------------------------
if [[ $fails -gt 0 ]]; then
  printf '%d/%d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'ok - %d checks passed\n' "$checks"
