#!/usr/bin/env zsh
# Acceptance harness for arrlib.zsh.
# Run from the workspace root:  zsh test_arrlib.zsh
#
# arrlib.zsh is pure zsh: the harness empties PATH before sourcing, so any
# helper that forks out to an external tool fails loudly. The checks below
# lean on zsh array semantics on purpose — position 1 is the first element,
# ${#arr} is the element count, slices are inclusive [from,to] — because
# those are exactly the places ports from other shells go wrong.
emulate -R zsh
setopt no_unset
LC_ALL=C
export LC_ALL

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- ${0:h}

if [[ ! -f ./arrlib.zsh ]]; then
  print -r -- 'FAIL arrlib.zsh not found in the workspace root'
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

src_out=$(. ./arrlib.zsh 2>&1)
assert_eq 'sourcing is silent' '' "$src_out"

. ./arrlib.zsh

src_out=$(. ./arrlib.zsh 2>&1)
assert_eq 're-sourcing is silent (idempotent)' '' "$src_out"

. ./arrlib.zsh # re-sourcing in this shell must simply refresh the definitions

# ---- arr_len: element count, nothing else ---------------------------------------

typeset -a trio=( a bb ccc )
n='(never ran)'; arr_len n trio
assert_eq 'len counts elements' 3 "$n"

typeset -a lone=( hello )
n='(never ran)'; arr_len n lone
assert_eq 'len of a one-element array is 1, not the string length of the element' 1 "$n"

typeset -a nothing=()
n='(never ran)'; arr_len n nothing
assert_eq 'len of an empty array' 0 "$n"

# ---- arr_slice: 1-indexed, inclusive, negatives from the end ----------------------

typeset -a greek=( alpha beta gamma delta epsilon )
typeset -a cut

cut=( stale ); arr_slice cut greek 1 1
assert_arr 'position 1 is the FIRST element' cut alpha

cut=( stale ); arr_slice cut greek 2 4
assert_arr 'slice 2..4 is inclusive at both ends' cut beta gamma delta

cut=( stale ); arr_slice cut greek 1 -1
assert_arr 'slice 1..-1 is the whole array' cut alpha beta gamma delta epsilon

cut=( stale ); arr_slice cut greek -2 -1
assert_arr 'negative indices count from the end' cut delta epsilon

cut=( stale ); arr_slice cut greek 3 -2
assert_arr 'mixed positive/negative bounds' cut gamma delta

cut=( stale ); arr_slice cut greek 4 2
assert_arr 'from past to is an empty slice' cut

cut=( stale ); arr_slice cut greek 2 99
assert_arr 'to beyond the end clamps to the end' cut beta gamma delta epsilon

cut=( stale ); arr_slice cut greek -99 2
assert_arr 'from before the start clamps to the start' cut alpha beta

cut=( stale ); arr_slice cut greek -1 -2
assert_arr 'reversed negative bounds are empty' cut

cut=( stale ); arr_slice cut nothing 1 1
assert_arr 'slicing an empty array' cut

assert_arr 'slice never modifies the source' greek alpha beta gamma delta epsilon

# ---- arr_rotate: left rotation with wraparound --------------------------------------

typeset -a wheel=( a b c d )
typeset -a spun

spun=( stale ); arr_rotate spun wheel 0
assert_arr 'rotate by zero copies as-is' spun a b c d

spun=( stale ); arr_rotate spun wheel 1
assert_arr 'rotate left by one' spun b c d a

spun=( stale ); arr_rotate spun wheel 3
assert_arr 'rotate left by three' spun d a b c

spun=( stale ); arr_rotate spun wheel 4
assert_arr 'rotate by the length is a full circle' spun a b c d

spun=( stale ); arr_rotate spun wheel 6
assert_arr 'rotation count wraps around' spun c d a b

spun=( stale ); arr_rotate spun lone 9
assert_arr 'rotating a single element changes nothing' spun hello

spun=( stale ); arr_rotate spun nothing 5
assert_arr 'rotating an empty array stays empty' spun

assert_arr 'rotate never modifies the source' wheel a b c d

# ---- arr_unique: first occurrence wins ------------------------------------------------

typeset -a dups=( a b a c b )
typeset -a uniq_r

uniq_r=( stale ); arr_unique uniq_r dups
assert_arr 'first occurrence wins' uniq_r a b c

typeset -a cased=( a A a )
uniq_r=( stale ); arr_unique uniq_r cased
assert_arr 'unique is case-sensitive' uniq_r a A

assert_arr 'unique never modifies the source' dups a b a c b

# ---- arr_zip: interleave up to the shorter length ---------------------------------------

typeset -a nums=( 1 2 3 ) abc=( a b c )
typeset -a laced

laced=( stale ); arr_zip laced nums abc
assert_arr 'zip of equal-length arrays' laced 1 a 2 b 3 c

typeset -a short_b=( a )
laced=( stale ); arr_zip laced nums short_b
assert_arr 'zip stops at the shorter array (b shorter)' laced 1 a

typeset -a short_a=( 1 )
laced=( stale ); arr_zip laced short_a abc
assert_arr 'zip stops at the shorter array (a shorter)' laced 1 a

laced=( stale ); arr_zip laced nothing abc
assert_arr 'zip with an empty side is empty' laced

typeset -a spaced=( 'x y' z ) tail_q=( p 'q r' )
laced=( stale ); arr_zip laced spaced tail_q
assert_arr 'zipped elements with spaces stay whole' laced 'x y' p z 'q r'

# ---- arr_index_of: first exact match, 1-based, 0 when absent ------------------------------

i='(never ran)'; arr_index_of i greek alpha
assert_eq 'a match in the first slot reports 1' 1 "$i"

i='(never ran)'; arr_index_of i greek delta
assert_eq 'position of a later element' 4 "$i"

i='(never ran)'; arr_index_of i greek omega
assert_eq 'absent element reports 0' 0 "$i"

typeset -a starry=( x '*' y )
i='(never ran)'; arr_index_of i starry '*'
assert_eq 'a star needle matches only the literal star element' 2 "$i"

typeset -a qmark=( bad 'b?d' )
i='(never ran)'; arr_index_of i qmark 'b?d'
assert_eq 'pattern characters in the needle have no power' 2 "$i"

typeset -a echoes=( r s r )
i='(never ran)'; arr_index_of i echoes r
assert_eq 'duplicates report the FIRST position' 1 "$i"

i='(never ran)'; arr_index_of i nothing zz
assert_eq 'searching an empty array reports 0' 0 "$i"

# ---- destination-name robustness -----------------------------------------------------------
# Callers pick arbitrary destination names; unprefixed internals capture them.

s='(never ran)'; arr_len s trio
assert_eq 'destination may be named s' 3 "$s"

typeset -a out=( junk junk junk junk junk junk )
arr_slice out greek 1 2
assert_arr 'a longer stale destination is fully replaced' out alpha beta

result='(never ran)'; arr_index_of result wheel c
assert_eq 'destination may be named result' 3 "$result"

# ---- caller option hygiene -------------------------------------------------------------------
# The library gets called from scripts running with unusual options switched
# on. Each helper localizes its own options, so caller-side ksh_arrays (which
# would shift every index by one), sh_word_split, and glob_subst must change
# nothing about the results.

typeset -a odd_cut=( stale )
odd_i=''
setopt ksh_arrays sh_word_split glob_subst
arr_slice odd_cut greek 1 2
arr_index_of odd_i greek beta
unsetopt ksh_arrays sh_word_split glob_subst
assert_arr 'slice stays 1-indexed under unusual caller options' odd_cut alpha beta
assert_eq 'index_of stays 1-based under unusual caller options' 2 "$odd_i"

assert_eq 'PATH is still empty after all calls' '' "${PATH-unset}"

# ---- summary -----------------------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
