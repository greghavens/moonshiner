#!/usr/bin/env bash
# Acceptance harness for table.sh.
# Run from the workspace root:  bash test_table.sh
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

if [[ ! -f table.sh ]]; then
  printf 'FAIL table.sh not found in the workspace root\n'
  exit 1
fi

trun() { run_in "$T" bash "$ROOT/table.sh" "$@"; }

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

# expected tables are built line by line; no line ever ends in a space
EXP=''
exp_reset() { EXP=''; }
addline() { printf -v EXP '%s%s\n' "$EXP" "$1"; }

nl=$'\n'

# ---- fixtures ---------------------------------------------------------------

printf '%s\n' \
  'item,qty,unit price,notes' \
  'widget,12,3.50,fast mover' \
  'long-tail adapter cable spool,3,17.25,check reorder threshold soon' \
  'gizmo,-4,0.99,' > "$T/inventory.csv"

printf '%s\n' \
  'reading' \
  '123456789012' \
  '7' > "$T/readings.csv"

printf '%s\n' \
  'a,b,c' \
  ' 7,1e3,5' \
  '3,2,' > "$T/mixed.csv"

printf '%s\n' 'name,team' > "$T/headonly.csv"

: > "$T/empty.csv"

printf '%s\n' \
  'a,b,c' \
  '1,2,3' \
  '1,2' > "$T/badcount.csv"

printf '%s\n' \
  'x,y,z' \
  'a,b,' > "$T/trailing.csv"

printf '%s\n' \
  'count' \
  '3' \
  '11' > "$T/count.csv"

# ---- rendering, default maxwidth (16) ---------------------------------------

exp_reset
addline 'item             | qty | unit price | notes'
addline '-----------------+-----+------------+-----------------'
addline 'widget           |  12 |       3.50 | fast mover'
addline 'long-tail ada... |   3 |      17.25 | check reorder...'
addline 'gizmo            |  -4 |       0.99 |'
trun inventory.csv
expect "default maxwidth render" 0 "$EXP" ""

# ---- rendering, maxwidth 8 ---------------------------------------------------

exp_reset
addline 'item     | qty | unit ... | notes'
addline '---------+-----+----------+---------'
addline 'widget   |  12 |     3.50 | fast ...'
addline 'long-... |   3 |    17.25 | check...'
addline 'gizmo    |  -4 |     0.99 |'
trun inventory.csv 8
expect "maxwidth 8 render" 0 "$EXP" ""

# ---- numeric column stays right-aligned when its long cell truncates --------

exp_reset
addline ' reading'
addline '--------'
addline '12345...'
addline '       7'
trun readings.csv 8
expect "numeric detection uses original values" 0 "$EXP" ""

# ---- alignment disqualifiers: leading space, 1e3, empty cell ----------------

exp_reset
addline 'a  | b   | c'
addline '---+-----+--'
addline ' 7 | 1e3 | 5'
addline '3  | 2   |'
trun mixed.csv
expect "non-numeric cells force left alignment" 0 "$EXP" ""

# ---- header-only file ---------------------------------------------------------

exp_reset
addline 'name | team'
addline '-----+-----'
trun headonly.csv
expect "header-only file renders header and separator" 0 "$EXP" ""

# ---- single numeric column ----------------------------------------------------

exp_reset
addline 'count'
addline '-----'
addline '    3'
addline '   11'
trun count.csv
expect "single numeric column" 0 "$EXP" ""

# ---- trailing empty field is a real field -------------------------------------

exp_reset
addline 'x | y | z'
addline '--+---+--'
addline 'a | b |'
trun trailing.csv
expect "trailing comma yields an empty third field" 0 "$EXP" ""

# ---- data errors ---------------------------------------------------------------

trun empty.csv
expect "empty input" 65 "" "table.sh: empty input$nl"

trun badcount.csv
expect "field-count mismatch reports first offending line" 65 "" "table.sh: line 3: expected 3 fields, got 2$nl"

# ---- usage errors ---------------------------------------------------------------

trun inventory.csv 4
expect "maxwidth below minimum" 64 "" "table.sh: maxwidth must be an integer >= 5$nl"

trun inventory.csv wide
expect "maxwidth not a number" 64 "" "table.sh: maxwidth must be an integer >= 5$nl"

trun
expect "no arguments" 64 "" "usage: table.sh <file.csv> [maxwidth]$nl"

trun nope.csv
expect "unreadable file" 66 "" "table.sh: cannot read: nope.csv$nl"

# ---- summary --------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
