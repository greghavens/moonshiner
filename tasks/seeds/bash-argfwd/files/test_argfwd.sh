#!/usr/bin/env bash
# Regression harness for the release.sh -> deploy.sh handoff.
# Run from the workspace root:  bash test_argfwd.sh
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
cleanup() { rm -rf "$ROOT/$T" "$ROOT/request.log" "$ROOT/notes"; }
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

assert_true() { # assert_true <label> <rc-of-condition>
  checks=$((checks + 1))
  if [[ "$2" -eq 0 ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n' "$1"
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

if [[ ! -f deploy.sh || ! -f release.sh ]]; then
  printf 'FAIL deploy.sh and release.sh must both exist in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

SPOOL=''
read_spool() { # byte-exact request.log contents into SPOOL ('' if absent)
  SPOOL=''
  if [[ -f "$ROOT/request.log" ]]; then
    slurp SPOOL "$ROOT/request.log"
  fi
}

nl=$'\n'

# real files that a stray pattern in a field would match
mkdir -p notes
printf 'a\n' > notes/a.md
printf 'b\n' > notes/b.md

# ---- downstream tool invoked directly (control) -----------------------------

rm -f request.log
run_in "$ROOT" bash deploy.sh --message "two words" --target web1
expect "deploy.sh direct, multi-word field" 0 "queued 4 field(s)$nl" ""
printf -v exp 'argc=4\n--message\ntwo words\n--target\nweb1\n'
read_spool
assert_eq "deploy.sh direct: spool records fields verbatim" "$exp" "$SPOOL"

rm -f request.log
run_in "$ROOT" bash deploy.sh
expect "deploy.sh direct, no fields" 65 "" "deploy.sh: nothing to queue$nl"
[[ ! -e "$ROOT/request.log" ]]; assert_true "deploy.sh direct, no fields: spool untouched" "$?"

# ---- wrapper -----------------------------------------------------------------

rm -f request.log
run_in "$ROOT" bash release.sh --target web1
expect "wrapper, single-word fields" 0 "release: queueing via deploy.sh${nl}queued 4 field(s)$nl" ""
printf -v exp 'argc=4\n--channel\nstable\n--target\nweb1\n'
read_spool
assert_eq "wrapper, single-word fields: spool" "$exp" "$SPOOL"

rm -f request.log
run_in "$ROOT" bash release.sh --message "hotfix for cart totals" --target web1
expect "wrapper, multi-word field" 0 "release: queueing via deploy.sh${nl}queued 6 field(s)$nl" ""
printf -v exp 'argc=6\n--channel\nstable\n--message\nhotfix for cart totals\n--target\nweb1\n'
read_spool
assert_eq "wrapper, multi-word field: spool" "$exp" "$SPOOL"

rm -f request.log
run_in "$ROOT" bash release.sh --include "notes/*.md" --target web1
expect "wrapper, field that looks like a pattern" 0 "release: queueing via deploy.sh${nl}queued 6 field(s)$nl" ""
printf -v exp 'argc=6\n--channel\nstable\n--include\nnotes/*.md\n--target\nweb1\n'
read_spool
assert_eq "wrapper, field that looks like a pattern: spool" "$exp" "$SPOOL"

rm -f request.log
run_in "$ROOT" bash release.sh --note "" --target web1
expect "wrapper, intentionally blank field" 0 "release: queueing via deploy.sh${nl}queued 6 field(s)$nl" ""
printf -v exp 'argc=6\n--channel\nstable\n--note\n\n--target\nweb1\n'
read_spool
assert_eq "wrapper, intentionally blank field: spool" "$exp" "$SPOOL"

rm -f request.log
run_in "$ROOT" bash release.sh --note $'a\tb' --target web1
expect "wrapper, field containing a tab" 0 "release: queueing via deploy.sh${nl}queued 6 field(s)$nl" ""
printf -v exp 'argc=6\n--channel\nstable\n--note\na\tb\n--target\nweb1\n'
read_spool
assert_eq "wrapper, field containing a tab: spool" "$exp" "$SPOOL"

rm -f request.log
run_in "$ROOT" bash release.sh
expect "wrapper, no fields" 64 "" "usage: release.sh <deploy fields...>$nl"
[[ ! -e "$ROOT/request.log" ]]; assert_true "wrapper, no fields: spool untouched" "$?"

# ---- summary -----------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
