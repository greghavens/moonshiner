#!/usr/bin/env zsh
# Acceptance harness for stockrep.zsh.
# Run from the workspace root:  zsh test_assocrep.zsh
#
# stockrep.zsh must be pure zsh builtins, so every invocation below runs it
# through an absolute zsh path with PATH emptied: any fork to an external
# tool (sort, awk, ...) fails loudly and pollutes the byte-exact streams.
emulate -R zsh
setopt no_unset
LC_ALL=C
export LC_ALL
unset CDPATH cdpath 2>/dev/null

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- ${0:h}

zmodload zsh/mapfile

ZSHBIN=${commands[zsh]-}
if [[ -z $ZSHBIN ]]; then
  print -r -- 'FAIL could not resolve an absolute path to zsh'
  exit 1
fi

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
run_rep() { # run_rep <dir> <arg...> -- run stockrep.zsh with an empty PATH
  local d=$1
  shift
  ( cd -- "$d" && PATH='' exec "$ZSHBIN" "$ROOT/stockrep.zsh" "$@" ) \
    > "$ROOT/$T/out" 2> "$ROOT/$T/err"
  RC=$?
  OUT=${mapfile[$ROOT/$T/out]-}
  ERR=${mapfile[$ROOT/$T/err]-}
}

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

if [[ ! -f stockrep.zsh ]]; then
  print -r -- 'FAIL stockrep.zsh not found in the workspace root'
  exit 1
fi

# ---- fixtures ---------------------------------------------------------------
# The ledger deliberately ends WITHOUT a trailing newline on its last line;
# that line still counts. Line 8 restates glass.beaker, so its quantities
# accumulate. The malformed lines cover: no '=', empty category, empty item,
# non-numeric count, empty key, space inside the key, space inside the count,
# and a key with no category dot at all.

{
  print -rl -- \
    '# lab restock ledger, week 28' \
    'glass.beaker=12' \
    'metal.clamp=30' \
    'glass.flask=7' \
    '' \
    'paper.filter=200' \
    'metal.stand=4' \
    'glass.beaker=5' \
    'ink.cart=015' \
    'plastic.tube=0' \
    'glass.tall.jar=3' \
    'oops no equals here' \
    '.dotless=3' \
    'metal.=9' \
    'paper.sheet=twelve' \
    '=44' \
    'metal.clamp =8' \
    'paper.a4= 6' \
    '# end of ledger'
  print -rn -- 'count=9'
} > "$T/supplies.dat"

print -rl -- '# nothing delivered' '' '# see you next week' > "$T/quiet.dat"

usage_line='usage: stockrep.zsh <ledger> [first PATTERN | count PATTERN]'
TAB=$'\t'

# ---- full report ---------------------------------------------------------------

mkexp exp_report \
  'CATEGORIES' \
  "glass${TAB}3${TAB}27" \
  "ink${TAB}1${TAB}15" \
  "metal${TAB}2${TAB}34" \
  "paper${TAB}1${TAB}200" \
  "plastic${TAB}1${TAB}0" \
  'ITEMS' \
  "glass.beaker${TAB}17" \
  "glass.flask${TAB}7" \
  "glass.tall.jar${TAB}3" \
  "ink.cart${TAB}15" \
  "metal.clamp${TAB}30" \
  "metal.stand${TAB}4" \
  "paper.filter${TAB}200" \
  "plastic.tube${TAB}0" \
  'MALFORMED' \
  '.dotless=3' \
  '=44' \
  'count=9' \
  'metal.=9' \
  'metal.clamp =8' \
  'oops no equals here' \
  'paper.a4= 6' \
  'paper.sheet=twelve' \
  'TOTALS' \
  "categories${TAB}5" \
  "items${TAB}8" \
  "units${TAB}276" \
  "malformed${TAB}8"

run_rep "$T" supplies.dat
expect 'full report' 0 "$exp_report" ''
first_out=$OUT

run_rep "$T" supplies.dat
expect 'full report, second run' 0 "$exp_report" ''
assert_eq 'report is byte-stable across runs' "$first_out" "$OUT"

# ---- a ledger with no data lines at all -------------------------------------------

mkexp exp_quiet \
  'CATEGORIES' \
  'ITEMS' \
  'MALFORMED' \
  'TOTALS' \
  "categories${TAB}0" \
  "items${TAB}0" \
  "units${TAB}0" \
  "malformed${TAB}0"

run_rep "$T" quiet.dat
expect 'comments-and-blanks-only ledger' 0 "$exp_quiet" ''

# ---- first: one matching key via the (i) subscript flag -----------------------------

mkexp exp_one "glass.flask${TAB}7"
run_rep "$T" supplies.dat first '*.flask'
expect 'first with a tail pattern' 0 "$exp_one" ''

mkexp exp_one "ink.cart${TAB}15"
run_rep "$T" supplies.dat first 'ink.*'
expect 'first with a category pattern' 0 "$exp_one" ''

mkexp exp_one "glass.beaker${TAB}17"
run_rep "$T" supplies.dat first glass.beaker
expect 'first with an exact key reports accumulated units' 0 "$exp_one" ''

mkexp exp_err 'stockrep.zsh: no item matches: *.widget'
run_rep "$T" supplies.dat first '*.widget'
expect 'first with no match' 1 '' "$exp_err"

# ---- count: matching keys via the (I) subscript flag ----------------------------------

mkexp exp_cnt \
  '3' \
  "glass.beaker${TAB}17" \
  "glass.flask${TAB}7" \
  "glass.tall.jar${TAB}3"
run_rep "$T" supplies.dat count 'glass.*'
expect 'count lists every glass item in key order' 0 "$exp_cnt" ''

mkexp exp_cnt \
  '1' \
  "metal.clamp${TAB}30"
run_rep "$T" supplies.dat count '*.clamp'
expect 'count with a single match' 0 "$exp_cnt" ''

mkexp exp_cnt '0'
run_rep "$T" supplies.dat count 'zz.*'
expect 'count with no matches still answers' 0 "$exp_cnt" ''

# ---- argument errors --------------------------------------------------------------------

mkexp exp_usage "$usage_line"
run_rep "$T"
expect 'no arguments' 64 '' "$exp_usage"

mkexp exp_err 'stockrep.zsh: cannot read: nope.dat'
run_rep "$T" nope.dat
expect 'unreadable ledger' 66 '' "$exp_err"

mkexp exp_err 'stockrep.zsh: unknown mode: total' "$usage_line"
run_rep "$T" supplies.dat total
expect 'unknown mode' 64 '' "$exp_err"

mkexp exp_err 'stockrep.zsh: wrong number of arguments' "$usage_line"
run_rep "$T" supplies.dat first
expect 'first without a pattern' 64 '' "$exp_err"

run_rep "$T" supplies.dat count 'glass.*' extra
expect 'count with a stray argument' 64 '' "$exp_err"

# ---- summary ------------------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
