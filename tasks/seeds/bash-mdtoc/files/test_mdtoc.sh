#!/usr/bin/env bash
# Acceptance harness for mdtoc.sh.
# Run from the workspace root:  bash test_mdtoc.sh
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

if [[ ! -f mdtoc.sh ]]; then
  printf 'FAIL mdtoc.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

# ---- fixtures ---------------------------------------------------------------

printf '%s\n' \
  '# Deploy Guide' \
  '<!-- toc -->' \
  'stale entry one' \
  'stale entry two' \
  '<!-- tocstop -->' \
  '' \
  'Welcome to the deploy guide.' \
  '#deploys channel gets the announcements.' \
  '' \
  '## Setup' \
  'Install the tools.' \
  '' \
  '```sh' \
  '# not a heading, just a shell comment' \
  "echo '## also not a heading'" \
  '```' \
  '' \
  '##  Double  Space' \
  'Spacing torture test.' \
  '' \
  '## Install & Run' \
  'Run the installer.' \
  '' \
  '### The `--force` flag' \
  'When you need it.' \
  '' \
  '#### Rollback steps ' \
  'Numbered list lives here.' \
  '' \
  '##### Appendix note' \
  'Too deep for the TOC.' \
  '' \
  '## Setup' \
  'Second setup section.' \
  '' \
  '### Setup' \
  'Nested setup notes.' \
  '' \
  '# Café Ops' \
  'Unicode title.' \
  > "$T/guide.md"

printf -v exp_guide '%s\n' \
  '# Deploy Guide' \
  '<!-- toc -->' \
  '- [Deploy Guide](#deploy-guide)' \
  '  - [Setup](#setup)' \
  '  - [Double  Space](#double--space)' \
  '  - [Install & Run](#install--run)' \
  '    - [The `--force` flag](#the---force-flag)' \
  '      - [Rollback steps](#rollback-steps)' \
  '  - [Setup](#setup-1)' \
  '    - [Setup](#setup-2)' \
  '- [Café Ops](#caf-ops)' \
  '<!-- tocstop -->' \
  '' \
  'Welcome to the deploy guide.' \
  '#deploys channel gets the announcements.' \
  '' \
  '## Setup' \
  'Install the tools.' \
  '' \
  '```sh' \
  '# not a heading, just a shell comment' \
  "echo '## also not a heading'" \
  '```' \
  '' \
  '##  Double  Space' \
  'Spacing torture test.' \
  '' \
  '## Install & Run' \
  'Run the installer.' \
  '' \
  '### The `--force` flag' \
  'When you need it.' \
  '' \
  '#### Rollback steps ' \
  'Numbered list lives here.' \
  '' \
  '##### Appendix note' \
  'Too deep for the TOC.' \
  '' \
  '## Setup' \
  'Second setup section.' \
  '' \
  '### Setup' \
  'Nested setup notes.' \
  '' \
  '# Café Ops' \
  'Unicode title.'

printf '%s\n' \
  'notes file' \
  '```' \
  '# fenced pseudo heading' \
  '```' \
  '<!-- toc -->' \
  'leftover bullet' \
  '<!-- tocstop -->' \
  'tail text' \
  > "$T/notes.md"

printf -v exp_notes '%s\n' \
  'notes file' \
  '```' \
  '# fenced pseudo heading' \
  '```' \
  '<!-- toc -->' \
  '<!-- tocstop -->' \
  'tail text'

printf '%s\n' \
  '# Title' \
  ' <!-- toc -->' \
  'the marker above is indented, so it does not count' \
  > "$T/plain.md"

printf '%s\n' \
  '# Half' \
  '<!-- toc -->' \
  'no closing marker anywhere' \
  > "$T/half.md"

# ---- main document: extraction, slugs, nesting, replacement -------------------

run_in "$T" bash "$ROOT/mdtoc.sh" guide.md
expect "guide.md first run" 0 "" ""
slurp GOT "$T/guide.md"
assert_eq "guide.md rewritten with the generated TOC" "$exp_guide" "$GOT"

run_in "$T" bash "$ROOT/mdtoc.sh" guide.md
expect "guide.md second run" 0 "" ""
slurp GOT2 "$T/guide.md"
assert_eq "second run is byte-identical (idempotent)" "$exp_guide" "$GOT2"

# ---- no headings: the region empties out ---------------------------------------

run_in "$T" bash "$ROOT/mdtoc.sh" notes.md
expect "notes.md (no real headings)" 0 "" ""
slurp GOTN "$T/notes.md"
assert_eq "region emptied when no headings exist" "$exp_notes" "$GOTN"

run_in "$T" bash "$ROOT/mdtoc.sh" notes.md
expect "notes.md second run" 0 "" ""
slurp GOTN2 "$T/notes.md"
assert_eq "empty-region rewrite is idempotent" "$exp_notes" "$GOTN2"

# ---- marker problems leave the file alone ---------------------------------------

slurp PLAIN_BEFORE "$T/plain.md"
run_in "$T" bash "$ROOT/mdtoc.sh" plain.md
expect "indented marker does not count" 65 "" 'mdtoc.sh: no toc markers in plain.md'$'\n'
slurp PLAIN_AFTER "$T/plain.md"
assert_eq "plain.md untouched" "$PLAIN_BEFORE" "$PLAIN_AFTER"

slurp HALF_BEFORE "$T/half.md"
run_in "$T" bash "$ROOT/mdtoc.sh" half.md
expect "opening marker without a closer" 65 "" 'mdtoc.sh: no toc markers in half.md'$'\n'
slurp HALF_AFTER "$T/half.md"
assert_eq "half.md untouched" "$HALF_BEFORE" "$HALF_AFTER"

# ---- argument errors -------------------------------------------------------------

printf -v exp_usage 'usage: mdtoc.sh <file.md>\n'

run_in "$T" bash "$ROOT/mdtoc.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/mdtoc.sh" guide.md notes.md
expect "two arguments" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/mdtoc.sh" nope.md
expect "unreadable file" 66 "" 'mdtoc.sh: cannot read: nope.md'$'\n'

# ---- summary -----------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
