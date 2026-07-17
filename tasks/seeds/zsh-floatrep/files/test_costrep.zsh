#!/usr/bin/env zsh
# Acceptance harness for costrep.zsh.
# Run from the workspace root:  zsh test_costrep.zsh
#
# Rounding note, verified on this box's zsh 5.9: printf '%.2f' rounds a value
# sitting exactly on a half (representable in binary) to the EVEN last digit:
# 0.125 -> 0.12, 0.375 -> 0.38, 100.125 -> 100.12, 157.875 -> 157.88.
# The expected reports below bake that in; a hand-rolled half-away-from-zero
# rounder does not match them.
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

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

if [[ ! -f costrep.zsh ]]; then
  print -r -- 'FAIL costrep.zsh not found in the workspace root'
  exit 1
fi

# ---- mixed feed: repeats accumulate, decimals, a negative, a tab-separated row ---

{
  print -r -- '# storage bill, June'
  print -r -- 'cdn 12.5'
  print -r -- $'db\t41'
  print -r -- ''
  print -r -- 'cdn 7.5'
  print -r -- 'batch -3.25'
  print -r -- 'web 100.125'
} > "$T/june.txt"
mkexp exp_out \
  $'rows\t5' \
  $'services\t4' \
  $'total\t157.88' \
  $'mean\t39.47' \
  $'min\tbatch\t-3.25' \
  $'max\tweb\t100.12' \
  'SHARES' \
  $'cdn\t12.67' \
  $'db\t25.97' \
  $'batch\t-2.06' \
  $'web\t63.42'
run zsh costrep.zsh "$T/june.txt"
expect 'mixed feed' 0 "$exp_out" ''
first_out=$OUT

run zsh costrep.zsh "$T/june.txt"
assert_eq 'report is byte-stable across runs' "$first_out" "$OUT"

# ---- integer-only feed: everything still renders with two decimals --------------
# 1/800 and 3/800 of the total land exactly on 0.125% and 0.375%.

printf '%s\n' 'probe-a 1' 'probe-b 3' 'filler 796' > "$T/ints.txt"
mkexp exp_out \
  $'rows\t3' \
  $'services\t3' \
  $'total\t800.00' \
  $'mean\t266.67' \
  $'min\tprobe-a\t1.00' \
  $'max\tfiller\t796.00' \
  'SHARES' \
  $'probe-a\t0.12' \
  $'probe-b\t0.38' \
  $'filler\t99.50'
run zsh costrep.zsh "$T/ints.txt"
expect 'integer-only feed' 0 "$exp_out" ''

# ---- comma-locale caller: the report still uses decimal points -------------------

run env LC_ALL=de_DE.utf8 LC_NUMERIC=de_DE.utf8 zsh costrep.zsh "$T/ints.txt"
expect 'comma locale caller' 0 "$exp_out" ''

# ---- a zero total must not divide -------------------------------------------------

printf '%s\n' 'up 5' 'down -5' > "$T/zero.txt"
mkexp exp_out \
  $'rows\t2' \
  $'services\t2' \
  $'total\t0.00' \
  $'mean\t0.00' \
  $'min\tdown\t-5.00' \
  $'max\tup\t5.00' \
  'SHARES' \
  $'up\t0.00' \
  $'down\t0.00'
run zsh costrep.zsh "$T/zero.txt"
expect 'zero total' 0 "$exp_out" ''

# ---- single row, and ties resolve to the first name seen --------------------------

printf '%s\n' 'only 42' > "$T/one.txt"
mkexp exp_out \
  $'rows\t1' \
  $'services\t1' \
  $'total\t42.00' \
  $'mean\t42.00' \
  $'min\tonly\t42.00' \
  $'max\tonly\t42.00' \
  'SHARES' \
  $'only\t100.00'
run zsh costrep.zsh "$T/one.txt"
expect 'single row' 0 "$exp_out" ''

printf '%s\n' 'x 5' 'y 5' > "$T/tie.txt"
mkexp exp_out \
  $'rows\t2' \
  $'services\t2' \
  $'total\t10.00' \
  $'mean\t5.00' \
  $'min\tx\t5.00' \
  $'max\tx\t5.00' \
  'SHARES' \
  $'x\t50.00' \
  $'y\t50.00'
run zsh costrep.zsh "$T/tie.txt"
expect 'tied min and max take the first name' 0 "$exp_out" ''

# ---- bad rows warn with their line number and are skipped --------------------------

{
  print -r -- '# header'
  print -r -- 'web twelve'
  print -r -- 'web 1e3'
  print -r -- 'web 5.'
  print -r -- 'web .5'
  print -r -- 'lonely'
  print -r -- 'a b c'
  print -r -- 'web 2.5'
} > "$T/messy.txt"
mkexp exp_out \
  $'rows\t1' \
  $'services\t1' \
  $'total\t2.50' \
  $'mean\t2.50' \
  $'min\tweb\t2.50' \
  $'max\tweb\t2.50' \
  'SHARES' \
  $'web\t100.00'
mkexp exp_err \
  'costrep.zsh: line 2: bad row, skipped' \
  'costrep.zsh: line 3: bad row, skipped' \
  'costrep.zsh: line 4: bad row, skipped' \
  'costrep.zsh: line 5: bad row, skipped' \
  'costrep.zsh: line 6: bad row, skipped' \
  'costrep.zsh: line 7: bad row, skipped'
run zsh costrep.zsh "$T/messy.txt"
expect 'bad rows' 0 "$exp_out" "$exp_err"

# ---- nothing usable in the file ----------------------------------------------------

printf '%s\n' '# only padding' '' > "$T/padding.txt"
mkexp exp_err 'costrep.zsh: no data rows'
run zsh costrep.zsh "$T/padding.txt"
expect 'padding-only feed' 65 '' "$exp_err"

printf '%s\n' 'oops nan' > "$T/allbad.txt"
mkexp exp_err \
  'costrep.zsh: line 1: bad row, skipped' \
  'costrep.zsh: no data rows'
run zsh costrep.zsh "$T/allbad.txt"
expect 'only bad rows' 65 '' "$exp_err"

# ---- argument errors -----------------------------------------------------------------

mkexp exp_err 'usage: costrep.zsh <data-file>'
run zsh costrep.zsh
expect 'no argument' 64 '' "$exp_err"

mkexp exp_err "costrep.zsh: cannot read: $T/absent.txt"
run zsh costrep.zsh "$T/absent.txt"
expect 'missing data file' 66 '' "$exp_err"

# ---- summary --------------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
