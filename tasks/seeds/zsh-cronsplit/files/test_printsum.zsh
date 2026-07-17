#!/usr/bin/env zsh
# Acceptance harness for printsum.zsh.
# Run from the workspace root:  zsh test_printsum.zsh
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

if [[ ! -f printsum.zsh ]]; then
  print -r -- 'FAIL printsum.zsh not found in the workspace root'
  exit 1
fi
if [[ ! -f queue_feed.txt ]]; then
  print -r -- 'FAIL queue_feed.txt not found in the workspace root'
  exit 1
fi

# ---- the checked-in feed: full report ------------------------------------------

mkexp exp_out \
  'USAGE' \
  $'annex\t3\t20' \
  $'lobby-a\t3\t14' \
  $'mezz-2\t2\t46' \
  'WATCHED' \
  $'annex\t20' \
  $'lobby-a\t14' \
  'TOTALS' \
  $'jobs\t8' \
  $'printers\t3' \
  $'users\t4' \
  $'pages\t80'
mkexp exp_err \
  'printsum.zsh: line 9: malformed, skipped' \
  'printsum.zsh: line 11: malformed, skipped'
run zsh printsum.zsh queue_feed.txt
expect 'checked-in feed' 0 "$exp_out" "$exp_err"
first_out=$OUT

run zsh printsum.zsh queue_feed.txt
assert_eq 'report is byte-stable across runs' "$first_out" "$OUT"

# ---- one printer, one user, watched entry with no activity stays 0 --------------

printf '%s\n' 'annex|dana|10' 'annex|dana|5' > "$T/mini.txt"
mkexp exp_out \
  'USAGE' \
  $'annex\t2\t15' \
  'WATCHED' \
  $'annex\t15' \
  $'lobby-a\t0' \
  'TOTALS' \
  $'jobs\t2' \
  $'printers\t1' \
  $'users\t1' \
  $'pages\t15'
run zsh printsum.zsh "$T/mini.txt"
expect 'single-printer feed' 0 "$exp_out" ''

# ---- padding-only feed: sections still print, watch list one row per name -------

printf '%s\n' '# nothing exported tonight' '' '# still nothing' > "$T/quiet.txt"
mkexp exp_out \
  'USAGE' \
  'WATCHED' \
  $'annex\t0' \
  $'lobby-a\t0' \
  'TOTALS' \
  $'jobs\t0' \
  $'printers\t0' \
  $'users\t0' \
  $'pages\t0'
run zsh printsum.zsh "$T/quiet.txt"
expect 'padding-only feed' 0 "$exp_out" ''

# ---- a printer that is not on the watch list never shows up there ---------------

printf '%s\n' 'mezz-2|kim|4' > "$T/other.txt"
mkexp exp_out \
  'USAGE' \
  $'mezz-2\t1\t4' \
  'WATCHED' \
  $'annex\t0' \
  $'lobby-a\t0' \
  'TOTALS' \
  $'jobs\t1' \
  $'printers\t1' \
  $'users\t1' \
  $'pages\t4'
run zsh printsum.zsh "$T/other.txt"
expect 'unwatched printer' 0 "$exp_out" ''

# ---- malformed lines warn with their physical line number and are skipped -------

printf '%s\n' '# header' 'annex|amy' 'annex|amy|2|x' 'annex|amy|2' > "$T/warn.txt"
mkexp exp_out \
  'USAGE' \
  $'annex\t1\t2' \
  'WATCHED' \
  $'annex\t2' \
  $'lobby-a\t0' \
  'TOTALS' \
  $'jobs\t1' \
  $'printers\t1' \
  $'users\t1' \
  $'pages\t2'
mkexp exp_err \
  'printsum.zsh: line 2: malformed, skipped' \
  'printsum.zsh: line 3: malformed, skipped'
run zsh printsum.zsh "$T/warn.txt"
expect 'malformed lines' 0 "$exp_out" "$exp_err"

# ---- argument errors --------------------------------------------------------------

mkexp exp_err 'usage: printsum.zsh <feed-file>'
run zsh printsum.zsh
expect 'no argument' 64 '' "$exp_err"

mkexp exp_err "printsum.zsh: cannot read: $T/absent.txt"
run zsh printsum.zsh "$T/absent.txt"
expect 'missing feed file' 66 '' "$exp_err"

# ---- summary ------------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
