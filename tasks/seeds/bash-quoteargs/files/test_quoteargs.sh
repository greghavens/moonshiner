#!/usr/bin/env bash
# Acceptance harness for quote_args.sh (argv -> copy-pasteable command line).
# Run from the workspace root:  bash test_quoteargs.sh
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

[[ $0 == */* ]] && cd -- "${0%/*}"

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

if [[ ! -f quote_args.sh ]]; then
  printf 'FAIL quote_args.sh not found in the workspace root\n'
  exit 1
fi

# the library reconstructs lines; it must never need eval itself (the harness
# is the only place a rendered line gets evaluated)
checks=$((checks + 1))
if grep -Eq '(^|[^[:alnum:]_.])eval([^[:alnum:]_]|$)' quote_args.sh; then
  fails=$((fails + 1))
  printf 'FAIL library must not use eval\n'
fi

# ---- sourcing behavior --------------------------------------------------------
srcout=$(. ./quote_args.sh 2>&1)
assert_eq 'sourcing produces no output' '' "$srcout"

. ./quote_args.sh
. ./quote_args.sh   # sourcing twice must be harmless

for fn in qa_word qa_line; do
  checks=$((checks + 1))
  if ! declare -F "$fn" > /dev/null; then
    fails=$((fails + 1))
    printf 'FAIL function %s is not defined after sourcing\n' "$fn"
  fi
done

nl=$'\n'
tab=$'\t'
cr=$'\r'
sq=\'

# ---- qa_word: exact rendered forms --------------------------------------------
word_case() { # word_case <label> <input> <expected-rendering>
  local out='(stale)'
  qa_word out "$2"
  assert_eq "qa_word: $1" "$3" "$out"
}

word_case 'plain word passes through unquoted'    'deploy.log'        'deploy.log'
word_case 'safe punctuation passes through'       'a=b,c:%d/e.f@g+h-' 'a=b,c:%d/e.f@g+h-'
word_case 'empty string'                          ''                  "''"
word_case 'spaces get single quotes'              'Monthly Report $Q3.txt' "'Monthly Report \$Q3.txt'"
word_case 'apostrophe'                            "it${sq}s"          "'it'\\''s'"
word_case 'glob characters'                       '*.log'             "'*.log'"
word_case 'question mark'                         'what?.txt'         "'what?.txt'"
word_case 'leading tilde'                         '~backup'           "'~backup'"
word_case 'double quotes'                         'he said "go"'      "'he said \"go\"'"
word_case 'backslash without control chars'       'back\slash'        "'back\\slash'"
word_case 'option-looking word with a space'      '--title=Q3 (draft)' "'--title=Q3 (draft)'"
word_case 'tab uses ANSI-C form'                  "a${tab}b"          "\$'a\\tb'"
word_case 'newline uses ANSI-C form'              "two${nl}lines"     "\$'two\\nlines'"
word_case 'carriage return uses ANSI-C form'      "end${cr}"          "\$'end\\r'"
word_case 'quote and tab together'                "it${sq}s${tab}now" "\$'it\\'s\\tnow'"
word_case 'backslash beside a tab'                "a\\${tab}b"        "\$'a\\\\\\tb'"
word_case 'dollar stays literal in ANSI-C form'   "cost${tab}\$9"     "\$'cost\\t\$9'"

# destination-name hygiene: ordinary caller names must not collide with locals
s='(stale)'
qa_word s 'x y'
assert_eq 'qa_word: destination named s' "'x y'" "$s"
word='(stale)'
qa_word word 'plain'
assert_eq 'qa_word: destination named word' 'plain' "$word"

# ---- qa_line: joining ----------------------------------------------------------
line='(stale)'
qa_line line
assert_eq 'qa_line: no arguments renders an empty line' '' "$line"

qa_line line tar -czf 'quarterly reports.tgz' 'Monthly Report $Q3.txt'
assert_eq 'qa_line: mixed argv' \
  "tar -czf 'quarterly reports.tgz' 'Monthly Report \$Q3.txt'" "$line"

qa_line line grep -F "it${sq}s" notes.txt
assert_eq 'qa_line: apostrophe argument' "grep -F 'it'\\''s' notes.txt" "$line"

qa_line line printf '%s' '' end
assert_eq 'qa_line: empty argument survives in the middle' \
  "printf %s '' end" "$line"

# ---- round trips: eval the RENDERED line, must reproduce the argv exactly ------
roundtrip() { # roundtrip <label> <arg...>
  # locals deliberately avoid the library's __ prefix so the two namespaces
  # cannot collide in either direction
  local rt_label=$1
  shift
  local rt_orig=("$@")
  local rt_line=''
  qa_line rt_line "$@"
  eval "set -- $rt_line"
  checks=$((checks + 1))
  if [[ $# -ne ${#rt_orig[@]} ]]; then
    fails=$((fails + 1))
    printf 'FAIL roundtrip %s: argc %d became %d\n' "$rt_label" "${#rt_orig[@]}" "$#"
    return
  fi
  local rt_i=0
  local rt_a
  for rt_a in "$@"; do
    if [[ "$rt_a" != "${rt_orig[$rt_i]}" ]]; then
      fails=$((fails + 1))
      printf 'FAIL roundtrip %s: arg %d changed\n--- sent ---\n%s\n--- got back ---\n%s\n' \
        "$rt_label" "$rt_i" "${rt_orig[$rt_i]}" "$rt_a"
      return
    fi
    rt_i=$((rt_i + 1))
  done
}

roundtrip 'archive job'    tar -czf 'quarterly reports.tgz' -- 'Monthly Report $Q3.txt' 'budget (final) v2.csv'
roundtrip 'apostrophes'    grep -F "O${sq}Brien" 'staff list.txt'
roundtrip 'empty args'     printf '%s\n' '' 'x' ''
roundtrip 'tabs and newlines' log "col1${tab}col2" "line one${nl}line two"
roundtrip 'globs stay literal' ls '*.log' '?.tmp' '[abc].txt'
roundtrip 'tildes and dashes'  cp '~old' '-weird' './-weird'
roundtrip 'backslash soup'  echo 'C:\temp\new' "mix\\${tab}ed" '\\'
roundtrip 'dollars'         make 'TARGET=$HOME' '$(pwd)' '`date`'
roundtrip 'lone quote'      note "$sq"
roundtrip 'quote storm'     sh -c "echo ${sq}a b${sq}"

# a rendered line must survive a second render/eval cycle unchanged (idempotence
# of the round trip, not of the rendering)
first=''
qa_line first 'a b' "c${sq}d"
eval "set -- $first"
second=''
qa_line second "$@"
assert_eq 'render twice: stable' "$first" "$second"

# ---- summary -------------------------------------------------------------------
if [[ $fails -gt 0 ]]; then
  printf '%d/%d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'ok - %d checks passed\n' "$checks"
