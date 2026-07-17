#!/usr/bin/env bash
# Acceptance harness for fixup.sh.
# Run from the workspace root:  bash test_sedbatch.sh
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

assert_eq() { # assert_eq <label> <expected> <actual>
  checks=$((checks + 1))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

assert_true() { # assert_true <label> <rc-of-condition>
  checks=$((checks + 1))
  if [[ "$2" -eq 0 ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n' "$1"
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

if [[ ! -f fixup.sh ]]; then
  printf 'FAIL fixup.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

tree_state() { # tree_state <var> <dir> -- sorted listing + sha256 of every file
  local d=$1
  printf -v "$d" '%s' "$(cd "$ROOT/$T/$2" && find . -type f | LC_ALL=C sort | xargs sha256sum)"
}

EXPR='s|http://intranet\.example|https://intranet.example|g'

# ---- fixtures ---------------------------------------------------------------

W=work
mkdir -p "$T/$W/docs"

printf '%s\n' \
  'wiki home: http://intranet.example/wiki' \
  'build dashboard' \
  'links: http://intranet.example/ci and more' \
  'done' \
  > "$T/$W/docs/a.txt"

printf '%s\n' \
  'nothing to see' \
  'plain text' \
  > "$T/$W/docs/b.txt"

printf '%s\n' \
  'c: http://intranet.example/pages' \
  'tail line' \
  > "$T/$W/docs/c.txt"

slurp ORIG_A "$T/$W/docs/a.txt"
slurp ORIG_B "$T/$W/docs/b.txt"
slurp ORIG_C "$T/$W/docs/c.txt"

# ---- dry run: prints diffs, changes nothing -----------------------------------

printf -v exp_dry '%s\n' \
  '--- docs/a.txt' \
  '+++ docs/a.txt (fixed)' \
  '@@ -1,4 +1,4 @@' \
  '-wiki home: http://intranet.example/wiki' \
  '+wiki home: https://intranet.example/wiki' \
  ' build dashboard' \
  '-links: http://intranet.example/ci and more' \
  '+links: https://intranet.example/ci and more' \
  ' done' \
  '--- docs/c.txt' \
  '+++ docs/c.txt (fixed)' \
  '@@ -1,2 +1,2 @@' \
  '-c: http://intranet.example/pages' \
  '+c: https://intranet.example/pages' \
  ' tail line' \
  'would change 2 of 3 file(s)'

tree_state before_dry "$W"
run_in "$T/$W" bash "$ROOT/fixup.sh" --dry-run "$EXPR" 'docs/*.txt'
expect "dry run with pending changes" 1 "$exp_dry" ""
tree_state after_dry "$W"
assert_eq "dry run leaves the tree untouched" "$before_dry" "$after_dry"

# ---- dry run with nothing to change -------------------------------------------

run_in "$T/$W" bash "$ROOT/fixup.sh" --dry-run 's/zzz-not-here/x/' 'docs/*.txt'
expect "dry run, no pending changes" 0 'would change 0 of 3 file(s)'$'\n' ""

# ---- real run: backups + in-place edits ---------------------------------------

printf -v exp_real 'fixed: docs/a.txt\nfixed: docs/c.txt\nchanged 2 of 3 file(s)\n'

run_in "$T/$W" bash "$ROOT/fixup.sh" "$EXPR" 'docs/*.txt'
expect "real run" 0 "$exp_real" ""

printf -v exp_a_new '%s\n' \
  'wiki home: https://intranet.example/wiki' \
  'build dashboard' \
  'links: https://intranet.example/ci and more' \
  'done'
printf -v exp_c_new 'c: https://intranet.example/pages\ntail line\n'

slurp NOW_A "$T/$W/docs/a.txt"
slurp NOW_B "$T/$W/docs/b.txt"
slurp NOW_C "$T/$W/docs/c.txt"
assert_eq "a.txt rewritten in place" "$exp_a_new" "$NOW_A"
assert_eq "b.txt (no matches) left byte-identical" "$ORIG_B" "$NOW_B"
assert_eq "c.txt rewritten in place" "$exp_c_new" "$NOW_C"

slurp BAK_A "$T/$W/docs/a.txt.bak"
slurp BAK_C "$T/$W/docs/c.txt.bak"
assert_eq "a.txt.bak holds the pre-run bytes" "$ORIG_A" "$BAK_A"
assert_eq "c.txt.bak holds the pre-run bytes" "$ORIG_C" "$BAK_C"
[[ ! -e "$T/$W/docs/b.txt.bak" ]]; assert_true "unchanged file gets no backup" "$?"

# ---- second real run is a no-op ------------------------------------------------

tree_state before_rerun "$W"
run_in "$T/$W" bash "$ROOT/fixup.sh" "$EXPR" 'docs/*.txt'
expect "second real run reports zero changes" 0 'changed 0 of 3 file(s)'$'\n' ""
tree_state after_rerun "$W"
assert_eq "second real run leaves every byte in place" "$before_rerun" "$after_rerun"

# ---- backups are never edited, even when the glob catches them ------------------

run_in "$T/$W" bash "$ROOT/fixup.sh" --dry-run "$EXPR" 'docs/*'
expect "glob that also matches .bak files skips them" 0 'would change 0 of 3 file(s)'$'\n' ""

# ---- a bad expression touches nothing ------------------------------------------

mkdir -p "$T/$W/notes"
printf 'see http://intranet.example/faq\n' > "$T/$W/notes/n1.txt"

tree_state before_bad "$W"
run_in "$T/$W" bash "$ROOT/fixup.sh" 's|http://intranet\.example|https://x' 'notes/*.txt'
printf -v exp_bad_err 'fixup.sh: bad expression: s|http://intranet\\.example|https://x\n'
expect "syntactically bad expression" 65 "" "$exp_bad_err"
tree_state after_bad "$W"
assert_eq "bad expression leaves the tree untouched" "$before_bad" "$after_bad"

# ---- no files match --------------------------------------------------------------

run_in "$T/$W" bash "$ROOT/fixup.sh" --dry-run "$EXPR" 'docs/*.conf'
expect "glob with no matches" 66 "" 'fixup.sh: no files match: docs/*.conf'$'\n'

# ---- usage --------------------------------------------------------------------

printf -v exp_usage 'usage: fixup.sh [--dry-run] <sed-expression> <glob>\n'

run_in "$T/$W" bash "$ROOT/fixup.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T/$W" bash "$ROOT/fixup.sh" "$EXPR"
expect "missing glob argument" 64 "" "$exp_usage"

run_in "$T/$W" bash "$ROOT/fixup.sh" --force "$EXPR" 'docs/*.txt'
expect "unknown flag" 64 "" "$exp_usage"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
