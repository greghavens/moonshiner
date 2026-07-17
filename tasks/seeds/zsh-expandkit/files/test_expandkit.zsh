#!/usr/bin/env zsh
# Acceptance harness for expandkit.zsh.
# Run from the workspace root:  zsh test_expandkit.zsh
#
# expandkit.zsh targets hook scripts that run with an empty PATH, so this
# harness empties PATH before sourcing it: any helper that forks out to an
# external tool fails loudly here. Everything below is zsh builtins only.
emulate -R zsh
setopt no_unset
LC_ALL=C
export LC_ALL

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- ${0:h}

if [[ ! -f ./expandkit.zsh ]]; then
  print -r -- 'FAIL expandkit.zsh not found in the workspace root'
  exit 1
fi

PATH=''
# ---- builtins only from here on ---------------------------------------------

typeset -i checks=0 fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  (( checks += 1 ))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  (( fails += 1 ))
  printf 'FAIL %s\n  expected: [%s]\n  actual:   [%s]\n' "$1" "$2" "$3"
}

assert_arr() { # assert_arr <label> <arrayname> <expected element>...
  local __label=$1 __name=$2
  shift 2
  local -a __got=( "${(@P)__name}" ) __want=( "$@" )
  (( checks += 1 ))
  if (( ${#__got} == ${#__want} )) && [[ "${(pj:\x1f:)__got}" == "${(pj:\x1f:)__want}" ]]; then
    return 0
  fi
  (( fails += 1 ))
  printf 'FAIL %s\n  expected (%d): %s\n  actual   (%d): %s\n' \
    "$__label" ${#__want} "${(j:|:)${(@qq)__want}}" ${#__got} "${(j:|:)${(@qq)__got}}"
}

# ---- sourcing behaviour -------------------------------------------------------

src_out=$(. ./expandkit.zsh 2>&1)
assert_eq 'sourcing is silent' '' "$src_out"

. ./expandkit.zsh

src_out=$(. ./expandkit.zsh 2>&1)
assert_eq 're-sourcing is silent (idempotent)' '' "$src_out"

. ./expandkit.zsh # re-sourcing in this shell must simply refresh the definitions

assert_eq 'PATH is still empty after sourcing' '' "${PATH-unset}"

# ---- ek_split -----------------------------------------------------------------

typeset -a parts
parts=( stale ); ek_split parts ',' 'red,green,blue'
assert_arr 'split on a comma' parts red green blue

parts=( stale ); ek_split parts ',' 'a,,b'
assert_arr 'split keeps an empty middle field' parts a '' b

parts=( stale ); ek_split parts ',' ',a,'
assert_arr 'split keeps empty end fields' parts '' a ''

parts=( stale ); ek_split parts '::' 'one::two::three'
assert_arr 'split on a multi-character separator' parts one two three

parts=( stale ); ek_split parts ', ' 'a, b,c'
assert_arr 'the whole separator string must match' parts a 'b,c'

parts=( stale ); ek_split parts ',' 'plain'
assert_arr 'separator absent: one field' parts plain

parts=( stale ); ek_split parts ',' 'a b,c'
assert_arr 'fields with spaces stay single fields' parts 'a b' c

parts=( stale ); ek_split parts '*' 'a*b'
assert_arr 'separator is literal text, not a pattern' parts a b

parts=( stale stale ); ek_split parts ',' ''
assert_arr 'empty input: empty array' parts

# ---- ek_join ------------------------------------------------------------------

typeset -a src=( red green blue )
j='(never ran)'; ek_join j ',' src
assert_eq 'join with a comma' 'red,green,blue' "$j"

typeset -a holey=( a '' b )
j='(never ran)'; ek_join j ',' holey
assert_eq 'join keeps empty elements' 'a,,b' "$j"

parts=( stale ); ek_split parts ',' 'a,,b'
j='(never ran)'; ek_join j ',' parts
assert_eq 'split then join round-trips' 'a,,b' "$j"

j='(never ran)'; ek_join j ' -> ' src
assert_eq 'join with a multi-character separator' 'red -> green -> blue' "$j"

typeset -a nothing=()
j='(never ran)'; ek_join j ',' nothing
assert_eq 'join of an empty array' '' "$j"

typeset -a lone=( solo )
j='(never ran)'; ek_join j ',' lone
assert_eq 'join of a single element' 'solo' "$j"

typeset -a tricky=( 'a,b' c )
j='(never ran)'; ek_join j ';' tricky
assert_eq 'elements may contain other separator characters' 'a,b;c' "$j"

# ---- ek_upper / ek_lower --------------------------------------------------------

r='(never ran)'; ek_upper r 'make it so v2!'
assert_eq 'upper maps ASCII letters' 'MAKE IT SO V2!' "$r"

r='(never ran)'; ek_lower r 'Grep FLAGS'
assert_eq 'lower maps ASCII letters' 'grep flags' "$r"

r='(never ran)'; ek_lower r 'ABC123xyz'
assert_eq 'lower leaves digits alone' 'abc123xyz' "$r"

r='(never ran)'; ek_upper r 'naïve'
assert_eq 'upper passes non-ASCII bytes through (LC_ALL=C)' 'NAïVE' "$r"

r='(never ran)'; ek_upper r ''
assert_eq 'upper of the empty string' '' "$r"

# ---- ek_sorted / ek_rsorted ------------------------------------------------------

typeset -a mixed=( pear Apple apple 10 9 pear )
typeset -a sorted_r=( stale )
ek_sorted sorted_r mixed
assert_arr 'ascending byte order, duplicates kept' sorted_r 10 9 Apple apple pear pear
assert_arr 'source array is untouched by sorting' mixed pear Apple apple 10 9 pear

typeset -a rsorted_r=( stale )
ek_rsorted rsorted_r mixed
assert_arr 'descending byte order' rsorted_r pear pear apple Apple 9 10

typeset -a empty_src=()
sorted_r=( stale ); ek_sorted sorted_r empty_src
assert_arr 'sorting an empty array' sorted_r

# ---- ek_uniq ----------------------------------------------------------------------

typeset -a dups=( a b a c b )
typeset -a uniq_r=( stale )
ek_uniq uniq_r dups
assert_arr 'first occurrence wins' uniq_r a b c
assert_arr 'source array is untouched by uniq' dups a b a c b

typeset -a cased=( a A a )
uniq_r=( stale ); ek_uniq uniq_r cased
assert_arr 'uniq is case-sensitive' uniq_r a A

typeset -a hollow=( '' a '' b a )
uniq_r=( stale ); ek_uniq uniq_r hollow
assert_arr 'empty elements collapse to one' uniq_r '' a b

uniq_r=( stale ); ek_uniq uniq_r empty_src
assert_arr 'uniq of an empty array' uniq_r

# ---- ek_keys / ek_rkeys / ek_vals ---------------------------------------------------

typeset -A inv=( [metal.clamp]=30 [glass.beaker]=12 [Alloy.rod]=4 [glass.flask]=7 )

typeset -a ks=( stale )
ek_keys ks inv
assert_arr 'keys come out in ascending byte order' ks Alloy.rod glass.beaker glass.flask metal.clamp

ks=( stale ); ek_rkeys ks inv
assert_arr 'rkeys is the descending order' ks metal.clamp glass.flask glass.beaker Alloy.rod

typeset -a vs=( stale )
ek_vals vs inv
assert_arr 'values follow ascending key order' vs 4 12 7 30

typeset -A noted=( [motto]='two words' [x]=1 )
vs=( stale ); ek_vals vs noted
assert_arr 'a value with a space is one element' vs 'two words' 1

typeset -A bare=()
ks=( stale ); ek_keys ks bare
assert_arr 'keys of an empty map' ks
vs=( stale ); ek_vals vs bare
assert_arr 'values of an empty map' vs

# ---- destination-name robustness ------------------------------------------------------
# Callers pick arbitrary destination names; unprefixed internals capture them.

s='(never ran)'; ek_upper s 'abc'
assert_eq 'destination may be named s' 'ABC' "$s"

result='(never ran)'; ek_lower result 'ABC'
assert_eq 'destination may be named result' 'abc' "$result"

typeset -a out=( junk junk junk junk )
ek_split out ',' 'x,y'
assert_arr 'a longer stale destination is fully replaced' out x y

# ---- caller option hygiene --------------------------------------------------------------
# The library is called from scripts that run with unusual options switched
# on. Each helper localizes its own options, so caller-side sh_word_split,
# ksh_arrays, and glob_subst must change nothing about the results.

typeset -a odd_parts=( stale )
odd_up=''
setopt ksh_arrays sh_word_split glob_subst
ek_split odd_parts ',' 'a,b c,*'
ek_upper odd_up 'shout'
unsetopt ksh_arrays sh_word_split glob_subst
assert_arr 'split is unmoved by unusual caller options' odd_parts a 'b c' '*'
assert_eq 'upper is unmoved by unusual caller options' 'SHOUT' "$odd_up"

assert_eq 'PATH is still empty after all calls' '' "${PATH-unset}"

# ---- summary ------------------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
