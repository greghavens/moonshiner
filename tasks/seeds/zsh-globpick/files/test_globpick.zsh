#!/usr/bin/env zsh
# Acceptance harness for pick.zsh.
# Run from the workspace root:  zsh test_globpick.zsh
#
# The fixture tree is built fresh on every run. File sizes are exact byte
# counts, and every long-lived file gets a FIXED past mtime via touch -t, so
# size thresholds, mtime ordering, and the >N-days age split never depend on
# when the harness runs. cache.tmp is the one exception: created at run time,
# it is always the newest file and never more than a day old.
emulate -R zsh
setopt no_unset
LC_ALL=C
export LC_ALL
unset CDPATH cdpath 2>/dev/null

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- ${0:h}

zmodload zsh/mapfile

ROOT=$PWD
T=_t
rm -rf "$T"
mkdir -p "$T"
trap 'rm -rf "$ROOT/$T"' EXIT

typeset -i checks=0 fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  (( checks += 1 ))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  (( fails += 1 ))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

mkexp() { # mkexp <var> <line>... -- join lines with \n plus trailing newline
  local __n=$1
  shift
  : ${(P)__n::=${(F)@}$'\n'}
}

RC=0
OUT=''
ERR=''
run_in() { # run_in <dir> <cmd...> -- capture RC, OUT, ERR byte-exactly
  local d=$1
  shift
  ( cd -- "$d" && exec "$@" ) > "$ROOT/$T/out" 2> "$ROOT/$T/err"
  RC=$?
  OUT=${mapfile[$ROOT/$T/out]-}
  ERR=${mapfile[$ROOT/$T/err]-}
}

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

if [[ ! -f pick.zsh ]]; then
  print -r -- 'FAIL pick.zsh not found in the workspace root'
  exit 1
fi

# ---- fixture tree -----------------------------------------------------------
# sizes (bytes):        mtimes:
#   notes.txt      120    2026-01-01 12:00
#   two words.txt   80    2026-01-15 12:00
#   data.bin      2048    2026-02-01 12:00
#   build.log      600    2026-03-05 12:00
#   tools/run.zsh  300    2026-04-01 12:00
#   tools/deep/cache.tmp 600   (run time — always the newest)
#   .hushed         10    hidden: must never show up
#   empty/               directory with nothing in it

mkfix() { # mkfix <relpath> <bytes> -- file of exactly <bytes> 'x' bytes
  print -rn -- "${(l:$2::x:):-}" > "$T/grove/$1"
}

mkdir -p "$T/grove/tools/deep" "$T/grove/empty"
mkfix notes.txt 120
mkfix 'two words.txt' 80
mkfix data.bin 2048
mkfix build.log 600
mkfix tools/run.zsh 300
mkfix tools/deep/cache.tmp 600
mkfix .hushed 10
touch -t 202601011200 "$T/grove/notes.txt"
touch -t 202601151200 "$T/grove/two words.txt"
touch -t 202602011200 "$T/grove/data.bin"
touch -t 202603051200 "$T/grove/build.log"
touch -t 202604011200 "$T/grove/tools/run.zsh"

usage_line='usage: pick.zsh files|dirs|over|newest|stale <dir> [N]'

# ---- files: regular files only, recursive, name order --------------------------

mkexp exp_out \
  'build.log' \
  'data.bin' \
  'notes.txt' \
  'tools/deep/cache.tmp' \
  'tools/run.zsh' \
  'two words.txt'
run_in "$T" zsh "$ROOT/pick.zsh" files grove
expect 'files: plain files, name order, hidden excluded' 0 "$exp_out" ''
first_out=$OUT

run_in "$T" zsh "$ROOT/pick.zsh" files grove
assert_eq 'files listing is byte-stable across runs' "$first_out" "$OUT"

mkexp exp_out \
  'deep/cache.tmp' \
  'run.zsh'
run_in "$T" zsh "$ROOT/pick.zsh" files grove/tools
expect 'files: paths are relative to the directory argument' 0 "$exp_out" ''

# ---- dirs: directories only ------------------------------------------------------

mkexp exp_out \
  'empty' \
  'tools' \
  'tools/deep'
run_in "$T" zsh "$ROOT/pick.zsh" dirs grove
expect 'dirs: directories only, name order' 0 "$exp_out" ''

# ---- over: size strictly greater than N bytes ---------------------------------------

mkexp exp_out \
  'build.log' \
  'data.bin' \
  'tools/deep/cache.tmp'
run_in "$T" zsh "$ROOT/pick.zsh" over grove 500
expect 'over 500: the three larger files' 0 "$exp_out" ''

run_in "$T" zsh "$ROOT/pick.zsh" over grove 599
expect 'over 599: boundary from below' 0 "$exp_out" ''

mkexp exp_out 'data.bin'
run_in "$T" zsh "$ROOT/pick.zsh" over grove 600
expect 'over 600: strictly greater, the 600-byte files drop out' 0 "$exp_out" ''

# ---- newest: K most recently modified, newest first ----------------------------------

mkexp exp_out \
  'tools/deep/cache.tmp' \
  'tools/run.zsh'
run_in "$T" zsh "$ROOT/pick.zsh" newest grove 2
expect 'newest 2: mtime order, not name order' 0 "$exp_out" ''

mkexp exp_out \
  'tools/deep/cache.tmp' \
  'tools/run.zsh' \
  'build.log' \
  'data.bin' \
  'two words.txt' \
  'notes.txt'
run_in "$T" zsh "$ROOT/pick.zsh" newest grove 10
expect 'newest 10: more than exist means all of them' 0 "$exp_out" ''

# ---- stale: modified more than N days ago ---------------------------------------------

mkexp exp_out \
  'build.log' \
  'data.bin' \
  'notes.txt' \
  'tools/run.zsh' \
  'two words.txt'
run_in "$T" zsh "$ROOT/pick.zsh" stale grove 30
expect 'stale 30: the pinned-mtime files, name order' 0 "$exp_out" ''

# ---- empty results: exit 1 and total silence --------------------------------------------

run_in "$T" zsh "$ROOT/pick.zsh" over grove 999999
expect 'over: nothing that big' 1 '' ''

run_in "$T" zsh "$ROOT/pick.zsh" newest grove 0
expect 'newest 0: asks for nothing' 1 '' ''

run_in "$T" zsh "$ROOT/pick.zsh" files grove/empty
expect 'files in an empty directory' 1 '' ''

run_in "$T" zsh "$ROOT/pick.zsh" dirs grove/empty
expect 'dirs in an empty directory' 1 '' ''

# ---- argument errors ----------------------------------------------------------------------

mkexp exp_usage "$usage_line"
run_in "$T" zsh "$ROOT/pick.zsh"
expect 'no arguments' 64 '' "$exp_usage"

mkexp exp_err 'pick.zsh: wrong number of arguments' "$usage_line"
run_in "$T" zsh "$ROOT/pick.zsh" files
expect 'mode without directory' 64 '' "$exp_err"

run_in "$T" zsh "$ROOT/pick.zsh" files grove extra
expect 'files with a stray argument' 64 '' "$exp_err"

run_in "$T" zsh "$ROOT/pick.zsh" over grove
expect 'over without its byte count' 64 '' "$exp_err"

mkexp exp_err 'pick.zsh: unknown mode: sizes' "$usage_line"
run_in "$T" zsh "$ROOT/pick.zsh" sizes grove
expect 'unknown mode' 64 '' "$exp_err"

mkexp exp_err 'pick.zsh: not a number: 12x' "$usage_line"
run_in "$T" zsh "$ROOT/pick.zsh" over grove 12x
expect 'byte count is not a number' 64 '' "$exp_err"

mkexp exp_err 'pick.zsh: not a number: -1' "$usage_line"
run_in "$T" zsh "$ROOT/pick.zsh" newest grove -1
expect 'negative count is rejected' 64 '' "$exp_err"

mkexp exp_err 'pick.zsh: not a directory: nope'
run_in "$T" zsh "$ROOT/pick.zsh" files nope
expect 'missing directory' 66 '' "$exp_err"

# ---- summary --------------------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
