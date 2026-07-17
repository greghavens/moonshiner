#!/usr/bin/env bash
# Acceptance harness for sweepplan.sh.
# Run from the workspace root:  bash test_sweepplan.sh
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

if [[ ! -f sweepplan.sh ]]; then
  printf 'FAIL sweepplan.sh not found in the workspace root\n'
  exit 1
fi

nl=$'\n'

# ---- fixture tree: awkward real-world names copied off a shared drop ------

FX="$T/attic"
mkdir -p "$FX" "$FX/backups old" "$FX/empty-dir"
touch "$FX/quarterly report.log"
touch "$FX/"$'release\nnotes.log'
touch "$FX/-leading-dash.tmp"
touch "$FX/array[0].bak"
touch "$FX/what?.log"
touch "$FX/star*.tmp"
touch "$FX/café menu.txt"
touch "$FX/~"
touch "$FX/backups old/db snapshot.bak"
touch "$FX/REPORT.LOG"
touch "$FX/.hidden.log"
touch "$FX/old.bak.tmp"
touch "$FX/trace.log~"
touch "$FX/readme.txt"
touch "$FX/ padded.txt"
touch "$FX/draft .bak"

# expected manifest: rows in LC_ALL=C byte order of the RAW path; the
# embedded-newline name is displayed with the two characters backslash-n
: > "$T/exp"
add_exp() { printf '%s\t%s\n' "$1" "$2" >> "$T/exp"; }
add_exp KEEP    'attic/ padded.txt'
add_exp SWEEP   'attic/-leading-dash.tmp'
add_exp SWEEP   'attic/.hidden.log'
add_exp KEEP    'attic/REPORT.LOG'
add_exp ARCHIVE 'attic/array[0].bak'
add_exp ARCHIVE 'attic/backups old/db snapshot.bak'
add_exp KEEP    'attic/café menu.txt'
add_exp ARCHIVE 'attic/draft .bak'
add_exp SWEEP   'attic/old.bak.tmp'
add_exp SWEEP   'attic/quarterly report.log'
add_exp KEEP    'attic/readme.txt'
add_exp SWEEP   'attic/release\nnotes.log'
add_exp SWEEP   'attic/star*.tmp'
add_exp ARCHIVE 'attic/trace.log~'
add_exp SWEEP   'attic/what?.log'
add_exp ARCHIVE 'attic/~'
slurp EXPECTED "$T/exp"

# ---- the manifest ----------------------------------------------------------

run_in "$T" bash "$ROOT/sweepplan.sh" attic
expect "manifest over the drop tree" 0 "$EXPECTED" ""

# a planning script must be read-only: a second walk is byte-identical
first=$OUT
run_in "$T" bash "$ROOT/sweepplan.sh" attic
expect "second run is byte-identical" 0 "$first" ""

# ---- edges -----------------------------------------------------------------

mkdir -p "$T/empty-attic"
run_in "$T" bash "$ROOT/sweepplan.sh" empty-attic
expect "empty directory gives empty manifest" 0 "" ""

run_in "$T" bash "$ROOT/sweepplan.sh"
expect "no arguments" 64 "" "usage: sweepplan.sh <dir>$nl"

run_in "$T" bash "$ROOT/sweepplan.sh" nosuch
expect "missing directory" 66 "" "sweepplan.sh: not a directory: nosuch$nl"

# ---- summary ---------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
