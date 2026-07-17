#!/usr/bin/env bash
# Acceptance harness for packup.sh (manifest packer with guaranteed cleanup).
# Run from the workspace root:  bash test_trapclean.sh
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

note_fail() {
  fails=$((fails + 1))
  printf 'FAIL %s\n' "$1"
}

assert_eq() { # assert_eq <label> <expected> <actual>
  checks=$((checks + 1))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

assert_present() { # assert_present <label> <path>
  checks=$((checks + 1))
  if [[ -e "$2" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (missing: %s)\n' "$1" "$2"
}

assert_absent() { # assert_absent <label> <path>
  checks=$((checks + 1))
  if [[ ! -e "$2" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (still exists: %s)\n' "$1" "$2"
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

if [[ ! -f packup.sh ]]; then
  printf 'FAIL packup.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

nl=$'\n'

make_src() { # (re)build the standard source fixture under $T/src
  rm -rf "$T/src"
  mkdir -p "$T/src/nested"
  printf 'hello\n' > "$T/src/alpha.txt"
  printf 'abc' > "$T/src/b file.txt"
  : > "$T/src/zz.dat"
  printf 'not counted\n' > "$T/src/nested/inner.txt"
  printf 'hidden\n' > "$T/src/.hidden"
}

# job control on, so signal cases below can deliver INT to background runs
set -m

# ---- argument errors ---------------------------------------------------------

run_in "$T" bash "$ROOT/packup.sh"
expect "no arguments" 64 "" "usage: packup.sh <src-dir> <out-file>$nl"

run_in "$T" bash "$ROOT/packup.sh" only-one
expect "one argument" 64 "" "usage: packup.sh <src-dir> <out-file>$nl"

run_in "$T" bash "$ROOT/packup.sh" no-such-dir report.tsv
expect "missing source dir" 66 "" "packup.sh: not a directory: no-such-dir$nl"
assert_absent "missing source dir: no report written" "$T/report.tsv"

# ---- normal run ----------------------------------------------------------------

make_src
run_in "$T" bash "$ROOT/packup.sh" src report.tsv
expect "normal run" 0 "packed 3 file(s) -> report.tsv$nl" ""

printf -v exp_manifest 'alpha.txt\t6\nb file.txt\t3\nzz.dat\t0\ntotal\t9\n'
MAN=''
slurp MAN "$T/report.tsv"
assert_eq "normal run: manifest contents" "$exp_manifest" "$MAN"
assert_absent "normal run: staging file cleaned" "$T/report.tsv.part"
assert_absent "normal run: lock cleaned" "$T/report.tsv.lock"

# ready hook fires on a normal run too (no hold file involved)
rm -f "$T/report.tsv"
run_in "$T" env PACKUP_READY_FILE=ready.marker bash "$ROOT/packup.sh" src report.tsv
expect "run with ready hook" 0 "packed 3 file(s) -> report.tsv$nl" ""
assert_present "run with ready hook: marker touched" "$T/ready.marker"
assert_absent "run with ready hook: staging cleaned" "$T/report.tsv.part"
assert_absent "run with ready hook: lock cleaned" "$T/report.tsv.lock"
rm -f "$T/ready.marker"

# ---- re-run refreshes the report atomically ------------------------------------

printf 'data' > "$T/src/mm.log"
run_in "$T" bash "$ROOT/packup.sh" src report.tsv
expect "re-run over existing report" 0 "packed 4 file(s) -> report.tsv$nl" ""
printf -v exp_manifest2 'alpha.txt\t6\nb file.txt\t3\nmm.log\t4\nzz.dat\t0\ntotal\t13\n'
slurp MAN "$T/report.tsv"
assert_eq "re-run: manifest refreshed" "$exp_manifest2" "$MAN"

# ---- empty source dir -----------------------------------------------------------

mkdir -p "$T/empty"
run_in "$T" bash "$ROOT/packup.sh" empty empty.tsv
expect "empty source dir" 0 "packed 0 file(s) -> empty.tsv$nl" ""
printf -v exp_empty 'total\t0\n'
slurp MAN "$T/empty.tsv"
assert_eq "empty source dir: manifest" "$exp_empty" "$MAN"

# ---- lock held by someone else ---------------------------------------------------

make_src
rm -f "$T/report.tsv"
mkdir -p "$T/report.tsv.lock"
printf 'half-written\n' > "$T/report.tsv.part"   # another run's staging in flight
run_in "$T" bash "$ROOT/packup.sh" src report.tsv
expect "lock held" 75 "" "packup.sh: lock held: report.tsv.lock$nl"
assert_present "lock held: foreign lock left alone" "$T/report.tsv.lock"
assert_present "lock held: foreign staging left alone" "$T/report.tsv.part"
assert_absent "lock held: no report written" "$T/report.tsv"
slurp MAN "$T/report.tsv.part"
assert_eq "lock held: foreign staging unmodified" "half-written$nl" "$MAN"
rm -rf "$T/report.tsv.lock" "$T/report.tsv.part"

# ---- interrupted mid-run (INT) ----------------------------------------------------

signal_case() { # signal_case <label> <signal> <expected-rc> <extra-signal:0|1>
  local label=$1 sig=$2 want_rc=$3 twice=$4
  make_src
  rm -f "$T/report.tsv" "$T/ready.marker"
  : > "$T/hold.marker"
  ( cd "$T" && PACKUP_READY_FILE=ready.marker PACKUP_HOLD_FILE=hold.marker \
      exec bash "$ROOT/packup.sh" src report.tsv ) \
      > "$ROOT/$T/bg.out" 2> "$ROOT/$T/bg.err" &
  local pid=$!
  if wait_for "$label: script reports ready" "$T/ready.marker"; then
    assert_present "$label: staging exists mid-run" "$T/report.tsv.part"
    assert_present "$label: lock exists mid-run" "$T/report.tsv.lock"
    assert_absent "$label: final report absent mid-run" "$T/report.tsv"
    kill "-$sig" "$pid"
    if [[ "$twice" -eq 1 ]]; then
      kill "-$sig" "$pid" 2>/dev/null || true
    fi
  else
    kill -KILL "$pid" 2>/dev/null || true
  fi
  wait "$pid"
  RC=$?
  slurp OUT "$ROOT/$T/bg.out"
  slurp ERR "$ROOT/$T/bg.err"
  assert_eq "$label: exit code" "$want_rc" "$RC"
  assert_eq "$label: stdout" "" "$OUT"
  assert_eq "$label: stderr" "" "$ERR"
  assert_absent "$label: staging removed" "$T/report.tsv.part"
  assert_absent "$label: lock removed" "$T/report.tsv.lock"
  assert_absent "$label: no partial report" "$T/report.tsv"
  assert_present "$label: hold marker untouched" "$T/hold.marker"
  rm -f "$T/hold.marker" "$T/ready.marker"
  # the workspace must be immediately reusable: a plain follow-up run succeeds
  run_in "$T" bash "$ROOT/packup.sh" src report.tsv
  expect "$label: follow-up run" 0 "packed 3 file(s) -> report.tsv$nl" ""
  rm -f "$T/report.tsv"
}

signal_case "INT mid-run" INT 130 0
signal_case "TERM mid-run" TERM 143 0
signal_case "double INT mid-run" INT 130 1

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
