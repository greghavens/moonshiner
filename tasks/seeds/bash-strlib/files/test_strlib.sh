#!/usr/bin/env bash
# Acceptance harness for strlib.sh.
# Run from the workspace root:  bash test_strlib.sh
#
# strlib.sh targets early-boot hooks that run while PATH is empty, so this
# harness empties PATH before sourcing it: any helper that forks out to an
# external tool fails loudly here. Everything below uses bash builtins only.
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- "${0%/*}"

if [[ ! -f ./strlib.sh ]]; then
  printf 'FAIL strlib.sh not found in the workspace root\n'
  exit 1
fi

PATH=''
# ---- builtins only from here on --------------------------------------------

checks=0
fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  checks=$((checks + 1))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n  expected: [%s]\n  actual:   [%s]\n' "$1" "$2" "$3"
}

# ---- sourcing behaviour -----------------------------------------------------

src_out=$(. ./strlib.sh 2>&1)
assert_eq 'sourcing is silent' '' "$src_out"

. ./strlib.sh

src_out=$(. ./strlib.sh 2>&1)
assert_eq 're-sourcing is silent (idempotent)' '' "$src_out"

. ./strlib.sh # re-sourcing in this shell must simply refresh the definitions

assert_eq 'PATH is still empty after sourcing' '' "${PATH-unset}"

# ---- trim family ------------------------------------------------------------

r='(never ran)'; str_trim r '  hi  '
assert_eq 'str_trim strips both ends' 'hi' "$r"

r='(never ran)'; str_trim r $'\t x \r\n'
assert_eq 'str_trim handles tab/CR/LF' 'x' "$r"

r='(never ran)'; str_trim r ''
assert_eq 'str_trim of empty string' '' "$r"

r='(never ran)'; str_trim r '   '
assert_eq 'str_trim of all-whitespace' '' "$r"

r='(never ran)'; str_trim r 'a  b'
assert_eq 'str_trim keeps interior whitespace' 'a  b' "$r"

r='(never ran)'; str_trim r ' a  b '
assert_eq 'str_trim keeps interior, strips outer' 'a  b' "$r"

r='(never ran)'; str_ltrim r '  a  '
assert_eq 'str_ltrim strips left only' 'a  ' "$r"

r='(never ran)'; str_ltrim r $'\r\n\ta'
assert_eq 'str_ltrim handles tab/CR/LF' 'a' "$r"

r='(never ran)'; str_ltrim r $' \t '
assert_eq 'str_ltrim of all-whitespace' '' "$r"

r='(never ran)'; str_rtrim r '  a  '
assert_eq 'str_rtrim strips right only' '  a' "$r"

r='(never ran)'; str_rtrim r $'a\t\r\n'
assert_eq 'str_rtrim handles tab/CR/LF' 'a' "$r"

r='(never ran)'; str_rtrim r $' \t '
assert_eq 'str_rtrim of all-whitespace' '' "$r"

# ---- padding ----------------------------------------------------------------

r='(never ran)'; str_pad_left r '7' 3
assert_eq 'str_pad_left pads with spaces by default' '  7' "$r"

r='(never ran)'; str_pad_left r '7' 3 '0'
assert_eq 'str_pad_left honours the pad char' '007' "$r"

r='(never ran)'; str_pad_left r 'hello' 3
assert_eq 'str_pad_left never truncates' 'hello' "$r"

r='(never ran)'; str_pad_left r '' 3
assert_eq 'str_pad_left of empty string' '   ' "$r"

r='(never ran)'; str_pad_left r 'x' 4 'ab'
assert_eq 'str_pad_left uses only the first pad char' 'aaax' "$r"

r='(never ran)'; str_pad_left r 'x' 0
assert_eq 'str_pad_left width below 1 is a no-op' 'x' "$r"

r='(never ran)'; str_pad_right r '7' 3
assert_eq 'str_pad_right pads with spaces by default' '7  ' "$r"

r='(never ran)'; str_pad_right r 'ab' 5 '.'
assert_eq 'str_pad_right honours the pad char' 'ab...' "$r"

r='(never ran)'; str_pad_right r 'abc' 3
assert_eq 'str_pad_right at exact width is unchanged' 'abc' "$r"

r='(never ran)'; str_pad_right r '' 2 '-'
assert_eq 'str_pad_right of empty string' '--' "$r"

# ---- case mapping -----------------------------------------------------------

r='(never ran)'; str_upper r 'make it so v2!'
assert_eq 'str_upper maps ASCII letters' 'MAKE IT SO V2!' "$r"

r='(never ran)'; str_lower r 'Grep FLAGS'
assert_eq 'str_lower maps ASCII letters' 'grep flags' "$r"

r='(never ran)'; str_lower r 'ABC123xyz'
assert_eq 'str_lower leaves digits alone' 'abc123xyz' "$r"

r='(never ran)'; str_upper r 'naïve'
assert_eq 'str_upper passes non-ASCII bytes through (LC_ALL=C)' 'NAïVE' "$r"

# ---- replace_all ------------------------------------------------------------

r='(never ran)'; str_replace_all r 'a-b-c' '-' '+'
assert_eq 'str_replace_all replaces every occurrence' 'a+b+c' "$r"

r='(never ran)'; str_replace_all r 'axxb' '*' '!'
assert_eq 'str_replace_all treats * as literal text' 'axxb' "$r"

r='(never ran)'; str_replace_all r 'x[ab]y' '[ab]' ''
assert_eq 'str_replace_all treats [ab] as literal text' 'xy' "$r"

r='(never ran)'; str_replace_all r 'who?' '?' '!'
assert_eq 'str_replace_all replaces a literal ?' 'who!' "$r"

r='(never ran)'; str_replace_all r 'who' '?' '!'
assert_eq 'str_replace_all: absent literal ? changes nothing' 'who' "$r"

r='(never ran)'; str_replace_all r 'aaa' 'aa' 'b'
assert_eq 'str_replace_all is left-to-right, non-overlapping' 'ba' "$r"

r='(never ran)'; str_replace_all r 'abc' '' 'x'
assert_eq 'str_replace_all with empty needle is a no-op' 'abc' "$r"

r='(never ran)'; str_replace_all r 'a  b' '  ' ' '
assert_eq 'str_replace_all collapses runs when asked' 'a b' "$r"

# ---- basename / dirname clones ----------------------------------------------

r='(never ran)'; str_basename r 'a/b/c'
assert_eq 'basename a/b/c' 'c' "$r"

r='(never ran)'; str_basename r 'a/b/c/'
assert_eq 'basename ignores trailing slash' 'c' "$r"

r='(never ran)'; str_basename r 'a//b//'
assert_eq 'basename with doubled slashes' 'b' "$r"

r='(never ran)'; str_basename r 'c'
assert_eq 'basename of a bare name' 'c' "$r"

r='(never ran)'; str_basename r '/x'
assert_eq 'basename directly under root' 'x' "$r"

r='(never ran)'; str_basename r '/'
assert_eq 'basename of root' '/' "$r"

r='(never ran)'; str_basename r '///'
assert_eq 'basename of all slashes' '/' "$r"

r='(never ran)'; str_basename r ''
assert_eq 'basename of empty string' '.' "$r"

r='(never ran)'; str_dirname r 'a/b/c'
assert_eq 'dirname a/b/c' 'a/b' "$r"

r='(never ran)'; str_dirname r 'a/b/c///'
assert_eq 'dirname ignores trailing slashes' 'a/b' "$r"

r='(never ran)'; str_dirname r 'a/b/'
assert_eq 'dirname of a trailing-slash dir' 'a' "$r"

r='(never ran)'; str_dirname r 'a//b//c'
assert_eq 'dirname keeps interior doubled slashes' 'a//b' "$r"

r='(never ran)'; str_dirname r 'c'
assert_eq 'dirname of a bare name' '.' "$r"

r='(never ran)'; str_dirname r '/x'
assert_eq 'dirname directly under root' '/' "$r"

r='(never ran)'; str_dirname r '/'
assert_eq 'dirname of root' '/' "$r"

r='(never ran)'; str_dirname r '///'
assert_eq 'dirname of all slashes' '/' "$r"

r='(never ran)'; str_dirname r ''
assert_eq 'dirname of empty string' '.' "$r"

# ---- destination-name robustness ---------------------------------------------
# Callers pick arbitrary destination names; unprefixed internals capture them.

s='(never ran)'; str_upper s 'abc'
assert_eq 'destination may be named s' 'ABC' "$s"

result='(never ran)'; str_replace_all result 'abc' 'b' '+'
assert_eq 'destination may be named result' 'a+c' "$result"

out='stale'; str_lower out 'ABC'
assert_eq 'destination value is overwritten' 'abc' "$out"

assert_eq 'PATH is still empty after all calls' '' "${PATH-unset}"

# ---- summary ----------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
