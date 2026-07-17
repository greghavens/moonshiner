#!/usr/bin/env zsh
# Acceptance harness for jobcard.zsh.
# Run from the workspace root:  zsh test_zparse.zsh
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

if [[ ! -f jobcard.zsh ]]; then
  print -r -- 'FAIL jobcard.zsh not found in the workspace root'
  exit 1
fi

# Decoy files in the tool's working directory: if a tag like *.log were ever
# glob-expanded instead of used verbatim, these names would leak into the card
# and the byte-exact comparisons below would catch it.
: > "$T/a.log"
: > "$T/zz.log"

usage_line='usage: jobcard.zsh [-q] [-o FILE] [-r N] [-t TAG]... <job> [arg...]'
mkexp exp_help \
  "$usage_line" \
  '  -t, --tag TAG      add a label to the card (repeatable, order kept)' \
  '  -r, --retries N    retry budget, a non-negative integer (default 2)' \
  '  -o, --out FILE     log destination shown on the card (default out.log)' \
  '  -q, --quiet        mark the card quiet' \
  '  -h, --help         show this help'

# ---- help ---------------------------------------------------------------------

run_in "$T" zsh "$ROOT/jobcard.zsh" -h
expect 'short help' 0 "$exp_help" ''

run_in "$T" zsh "$ROOT/jobcard.zsh" --help
expect 'long help' 0 "$exp_help" ''

# ---- defaults -----------------------------------------------------------------

mkexp exp_card \
  'job: nightly' \
  'args: (none)' \
  'out: out.log' \
  'retries: 2' \
  'quiet: no' \
  'tags: (none)'
run_in "$T" zsh "$ROOT/jobcard.zsh" nightly
expect 'bare job name uses every default' 0 "$exp_card" ''

# ---- all options, separated values ----------------------------------------------

mkexp exp_card \
  'job: nightly' \
  'args: alpha beta' \
  'out: run.log' \
  'retries: 5' \
  'quiet: yes' \
  'tags: red,blue'
run_in "$T" zsh "$ROOT/jobcard.zsh" -t red --tag blue -o run.log -r 5 -q nightly alpha beta
expect 'separated option values' 0 "$exp_card" ''
first_out=$OUT

run_in "$T" zsh "$ROOT/jobcard.zsh" -t red --tag blue -o run.log -r 5 -q nightly alpha beta
assert_eq 'card is byte-stable across runs' "$first_out" "$OUT"

# ---- attached values: --opt=value and -ovalue -----------------------------------

mkexp exp_card \
  'job: batch-7' \
  'args: (none)' \
  'out: fast.log' \
  'retries: 7' \
  'quiet: no' \
  'tags: green'
run_in "$T" zsh "$ROOT/jobcard.zsh" --tag=green -ofast.log --retries=7 batch-7
expect 'attached option values' 0 "$exp_card" ''

# ---- repeated flags accumulate in command-line order -----------------------------

mkexp exp_card \
  'job: copy' \
  'args: (none)' \
  'out: out.log' \
  'retries: 2' \
  'quiet: no' \
  'tags: z,a,m,a'
run_in "$T" zsh "$ROOT/jobcard.zsh" -t z -t a --tag=m -t a copy
expect 'repeated tags keep order and duplicates' 0 "$exp_card" ''

# ---- options may follow positional arguments -------------------------------------

mkexp exp_card \
  'job: nightly' \
  'args: extra' \
  'out: out.log' \
  'retries: 2' \
  'quiet: yes' \
  'tags: late'
run_in "$T" zsh "$ROOT/jobcard.zsh" nightly -q -t late extra
expect 'options after the job name still parse' 0 "$exp_card" ''

# ---- double dash ends option parsing ----------------------------------------------

mkexp exp_card \
  'job: -t' \
  'args: nightly' \
  'out: out.log' \
  'retries: 2' \
  'quiet: yes' \
  'tags: (none)'
run_in "$T" zsh "$ROOT/jobcard.zsh" -q -- -t nightly
expect 'words after -- are positional' 0 "$exp_card" ''

# ---- tag values are opaque text ----------------------------------------------------

mkexp exp_card \
  'job: sweep' \
  'args: (none)' \
  'out: out.log' \
  'retries: 2' \
  'quiet: no' \
  'tags: two words,*.log'
run_in "$T" zsh "$ROOT/jobcard.zsh" -t 'two words' -t '*.log' sweep
expect 'tags with spaces and glob characters stay verbatim' 0 "$exp_card" ''

# ---- an explicitly empty tag is kept -------------------------------------------------

mkexp exp_card \
  'job: sweep' \
  'args: (none)' \
  'out: out.log' \
  'retries: 2' \
  'quiet: no' \
  'tags: '
run_in "$T" zsh "$ROOT/jobcard.zsh" -t '' sweep
expect 'one empty tag is not the same as no tags' 0 "$exp_card" ''

# ---- retries is echoed in decimal ------------------------------------------------------

mkexp exp_card \
  'job: nightly' \
  'args: (none)' \
  'out: out.log' \
  'retries: 7' \
  'quiet: no' \
  'tags: (none)'
run_in "$T" zsh "$ROOT/jobcard.zsh" --retries=007 nightly
expect 'leading zeros normalize to decimal' 0 "$exp_card" ''

# ---- repeating a boolean flag changes nothing -------------------------------------------

mkexp exp_card \
  'job: nightly' \
  'args: (none)' \
  'out: out.log' \
  'retries: 2' \
  'quiet: yes' \
  'tags: (none)'
run_in "$T" zsh "$ROOT/jobcard.zsh" -q --quiet nightly
expect 'quiet given twice is still just quiet' 0 "$exp_card" ''

# ---- usage errors: exit 64, empty stdout ---------------------------------------------------

mkexp exp_err \
  'jobcard.zsh: unknown option: -x' \
  "$usage_line"
run_in "$T" zsh "$ROOT/jobcard.zsh" -x nightly
expect 'unknown short option' 64 '' "$exp_err"

mkexp exp_err \
  'jobcard.zsh: unknown option: --wat' \
  "$usage_line"
run_in "$T" zsh "$ROOT/jobcard.zsh" --wat nightly
expect 'unknown long option' 64 '' "$exp_err"

mkexp exp_err \
  'jobcard.zsh: an option is missing its value' \
  "$usage_line"
run_in "$T" zsh "$ROOT/jobcard.zsh" nightly -t
expect 'trailing option with no value' 64 '' "$exp_err"

mkexp exp_err \
  'jobcard.zsh: missing job name' \
  "$usage_line"
run_in "$T" zsh "$ROOT/jobcard.zsh" -q
expect 'no job name given' 64 '' "$exp_err"

# ---- bad data: exit 65 ----------------------------------------------------------------------

mkexp exp_err 'jobcard.zsh: retries must be a non-negative integer, got: x7'
run_in "$T" zsh "$ROOT/jobcard.zsh" -r x7 nightly
expect 'non-numeric retries' 65 '' "$exp_err"

mkexp exp_err 'jobcard.zsh: retries must be a non-negative integer, got: -3'
run_in "$T" zsh "$ROOT/jobcard.zsh" --retries=-3 nightly
expect 'negative retries' 65 '' "$exp_err"

# ---- summary ---------------------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
