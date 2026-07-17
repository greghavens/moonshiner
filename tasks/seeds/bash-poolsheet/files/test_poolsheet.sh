#!/usr/bin/env bash
# Regression harness for render_week.sh (weekly schedule sheet renderer).
# Run from the workspace root:  bash test_poolsheet.sh
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

if [[ ! -f render_week.sh ]]; then
  printf 'FAIL render_week.sh not found in the workspace root\n'
  exit 1
fi

# ---- the script must parse: bash -n is part of the gate ----------------------

bash -n render_week.sh > "$T/nout" 2> "$T/nerr"
nrc=$?
slurp NERR "$T/nerr"
assert_eq "bash -n render_week.sh: exit code" 0 "$nrc"
assert_eq "bash -n render_week.sh: stderr" "" "$NERR"

# ---- fixtures -----------------------------------------------------------------

{
  printf '{{POOL}} -- week of {{WEEK}}\n'
  printf 'lane 1  06:00-08:00  masters\n'
  printf 'lane 2  06:00-08:00  open swim\n'
  printf 'lane 3  09:00-11:00  lessons\n'
  printf 'questions: {{DESK}}\n'
  printf 'posted {{WEEK}} at {{POOL}}\n'
} > "$T/board.tmpl"

{
  printf 'closed for maintenance all week\n'
  printf 'see you at the rec centre\n'
} > "$T/plain.tmpl"

# ---- argument handling ----------------------------------------------------------

run_in "$T" bash "$ROOT/render_week.sh"
expect "no arguments" 64 "" "usage: render_week.sh <template> <week-label>$nl"

run_in "$T" bash "$ROOT/render_week.sh" board.tmpl
expect "one argument" 64 "" "usage: render_week.sh <template> <week-label>$nl"

run_in "$T" bash "$ROOT/render_week.sh" absent.tmpl "July 20"
expect "missing template" 66 "" "render_week.sh: cannot read template: absent.tmpl$nl"

# ---- a normal render: every placeholder filled, every occurrence ----------------

printf -v exp_out '%s\n' \
  "O'Brien Aquatic Centre -- week of July 20" \
  'lane 1  06:00-08:00  masters' \
  'lane 2  06:00-08:00  open swim' \
  'lane 3  09:00-11:00  lessons' \
  'questions: front desk, ext. 4145' \
  "posted July 20 at O'Brien Aquatic Centre"
printf -v exp_err '%s\n' 'rendering week of July 20' 'render complete'

run_in "$T" bash "$ROOT/render_week.sh" board.tmpl "July 20"
expect "weekly board render" 0 "$exp_out" "$exp_err"

run_in "$T" bash "$ROOT/render_week.sh" board.tmpl "July 20"
expect "render is repeatable" 0 "$exp_out" "$exp_err"

# ---- a template with no placeholders passes through byte-for-byte ---------------

printf -v exp2_out '%s\n' 'closed for maintenance all week' 'see you at the rec centre'
printf -v exp2_err '%s\n' 'rendering week of Aug 3' 'render complete'

run_in "$T" bash "$ROOT/render_week.sh" plain.tmpl "Aug 3"
expect "placeholder-free template" 0 "$exp2_out" "$exp2_err"

# ---- summary ---------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
