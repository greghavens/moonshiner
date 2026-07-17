#!/usr/bin/env zsh
# Acceptance harness for sift.zsh.
# Run from the workspace root:  zsh test_sift.zsh
#
# Ordering note: expected listings are plain byte order (LC_ALL=C). The
# fixture deliberately contains report1.txt AND report100.txt — byte order
# puts report1.txt first ('.' sorts before '0'), while common UTF-8 locale
# collation flips them. One case below runs the tool under a de_DE
# environment to make sure the tool pins its own collation.
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
run() { # run <cmd...> -- capture RC, OUT, ERR byte-exactly
  "$@" > "$ROOT/$T/out" 2> "$ROOT/$T/err"
  RC=$?
  OUT=${mapfile[$ROOT/$T/out]-}
  ERR=${mapfile[$ROOT/$T/err]-}
}

run_in() { # run_in <dir> <cmd...> -- like run, but from another directory
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

if [[ ! -f sift.zsh ]]; then
  print -r -- 'FAIL sift.zsh not found in the workspace root'
  exit 1
fi

# ---- fixture tree -----------------------------------------------------------------

TREE=$T/tree
mkdir -p "$TREE/sub/deep" "$TREE/empty" "$TREE/reportdir.txt"
touch "$TREE/report1.txt" "$TREE/report07.txt" "$TREE/report23.txt" "$TREE/report100.txt"
touch "$TREE/readme.txt" "$TREE/README.md" "$TREE/Readme.TXT"
touch "$TREE/notes.log" "$TREE/data.csv"
touch "$TREE/two words.txt"
touch "$TREE/.hiddenreport.txt"
touch "$TREE/sub/report5.txt" "$TREE/sub/notes.log"
touch "$TREE/sub/deep/report99.txt"

usage_line='usage: sift.zsh [-c] <pattern> [dir]'

# ---- plain star pattern: files only, no dotfiles, no directories, byte order -------

mkexp exp_out \
  'readme.txt' \
  'report07.txt' \
  'report1.txt' \
  'report100.txt' \
  'report23.txt' \
  'two words.txt'
run zsh sift.zsh '*.txt' "$TREE"
expect 'star-dot-txt' 0 "$exp_out" ''
first_out=$OUT

# same answer when the caller's locale would collate differently
run env LC_ALL=de_DE.utf8 LC_COLLATE=de_DE.utf8 zsh sift.zsh '*.txt' "$TREE"
expect 'ordering is pinned against caller locale' 0 "$exp_out" ''

run zsh sift.zsh '*.txt' "$TREE"
assert_eq 'listing is byte-stable across runs' "$first_out" "$OUT"

# ---- numeric range selection ---------------------------------------------------------

mkexp exp_out \
  'report07.txt' \
  'report1.txt' \
  'report23.txt'
run zsh sift.zsh 'report<1-99>.txt' "$TREE"
expect 'numeric range excludes 100, matches zero-padded 07' 0 "$exp_out" ''

mkexp exp_out \
  'report07.txt' \
  'report1.txt' \
  'report100.txt'
run zsh sift.zsh 'report(<1-9>|<95-105>).txt' "$TREE"
expect 'alternation of two ranges' 0 "$exp_out" ''

# ---- recursive patterns --------------------------------------------------------------

mkexp exp_out \
  'report07.txt' \
  'report1.txt' \
  'report23.txt' \
  'sub/deep/report99.txt' \
  'sub/report5.txt'
run zsh sift.zsh '**/report<1-99>.txt' "$TREE"
expect 'recursive numeric range' 0 "$exp_out" ''

mkexp exp_out \
  'sub/notes.log'
run zsh sift.zsh 'sub/*.log' "$TREE"
expect 'pattern with a slash' 0 "$exp_out" ''

# ---- case-insensitive matching -------------------------------------------------------

mkexp exp_out \
  'README.md' \
  'Readme.TXT' \
  'readme.txt'
run zsh sift.zsh '(#i)readme*' "$TREE"
expect 'case-insensitive prefix' 0 "$exp_out" ''

# ---- negation, and negation combined with case-insensitivity -------------------------

mkexp exp_out \
  'README.md' \
  'Readme.TXT' \
  'data.csv' \
  'notes.log'
run zsh sift.zsh '^*.txt' "$TREE"
expect 'negation is case-sensitive by default' 0 "$exp_out" ''

mkexp exp_out \
  'README.md' \
  'data.csv' \
  'notes.log'
run zsh sift.zsh '(#i)^*.txt' "$TREE"
expect 'case-insensitive negation drops Readme.TXT too' 0 "$exp_out" ''

# ---- names with spaces come through intact --------------------------------------------

mkexp exp_out 'two words.txt'
run zsh sift.zsh 'two*' "$TREE"
expect 'space in a matched name' 0 "$exp_out" ''

# ---- directory defaults to where the caller stands -------------------------------------

mkexp exp_out 'data.csv'
run_in "$TREE" zsh "$ROOT/sift.zsh" '*.csv'
expect 'dir argument defaults to .' 0 "$exp_out" ''

# ---- no match: silence and exit 1 -------------------------------------------------------

run zsh sift.zsh '*.xyz' "$TREE"
expect 'no match' 1 '' ''

run zsh sift.zsh 'report<200-300>.txt' "$TREE"
expect 'range with no takers' 1 '' ''

# ---- count mode --------------------------------------------------------------------------

run zsh sift.zsh -c '**/*.log' "$TREE"
expect 'count of recursive matches' 0 $'2\n' ''

run zsh sift.zsh -c '*.xyz' "$TREE"
expect 'count of zero still reports, still exits 1' 1 $'0\n' ''

# ---- broken pattern syntax: our message, our exit code, no shell error spray -------------

mkexp exp_err 'sift.zsh: bad pattern: (#i'
run zsh sift.zsh '(#i' "$TREE"
expect 'unclosed (#i' 2 '' "$exp_err"

mkexp exp_err 'sift.zsh: bad pattern: ^('
run zsh sift.zsh -c '^(' "$TREE"
expect 'unclosed group under -c' 2 '' "$exp_err"

# ---- argument errors ----------------------------------------------------------------------

mkexp exp_err "$usage_line"
run zsh sift.zsh
expect 'no arguments' 64 '' "$exp_err"

run zsh sift.zsh '' "$TREE"
expect 'empty pattern' 64 '' "$exp_err"

run zsh sift.zsh '*.txt' "$TREE" extra
expect 'too many arguments' 64 '' "$exp_err"

run zsh sift.zsh -c
expect 'count flag with nothing to count' 64 '' "$exp_err"

mkexp exp_err "sift.zsh: not a directory: $TREE/report1.txt"
run zsh sift.zsh '*.txt' "$TREE/report1.txt"
expect 'search root is a file' 66 '' "$exp_err"

# ---- summary --------------------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
