#!/usr/bin/env zsh
# Acceptance harness for argshow.zsh (quoting-forms report tool).
# Run from the workspace root:  zsh test_argshow.zsh
emulate -L zsh
setopt no_unset
LC_ALL=C
export LC_ALL

[[ $0 == */* ]] && cd -- "${0%/*}"

ROOT=$PWD
T=_t
rm -rf "$T"
mkdir -p "$T"
TRAPEXIT() { rm -rf "$ROOT/$T"; }

typeset -i checks=0 fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  (( checks += 1 ))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  (( fails += 1 ))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

slurp() { # slurp <var> <file>
  typeset -g "$1"=
  [[ -f "$2" ]] || return 0
  IFS= read -r -d '' "$1" < "$2" || true
}

typeset RC OUT ERR
run_tool() { # run_tool <args...>
  zsh "$ROOT/argshow.zsh" "$@" > "$ROOT/$T/.out" 2> "$ROOT/$T/.err"
  RC=$?
  slurp OUT "$ROOT/$T/.out"
  slurp ERR "$ROOT/$T/.err"
}

if [[ ! -f argshow.zsh ]]; then
  printf 'FAIL argshow.zsh not found in the workspace root\n'
  exit 1
fi

nl=$'\n'
tab=$'\t'
sq=\'

# ---- 1. report mode: exact renderings of the three quoting forms -------------
run_tool report "Monthly Report \$Q3.txt" "it${sq}s" '' "a${tab}b" '*.log' 'back\slash'

expected="[1] raw: <report>${nl}"
expected+="[1] q: <report>${nl}"
expected+="[1] qq: <'report'>${nl}"
expected+="[1] q-: <report>${nl}"
expected+="[1] rt: ok${nl}"
expected+="[2] raw: <Monthly Report \$Q3.txt>${nl}"
expected+="[2] q: <Monthly\\ Report\\ \\\$Q3.txt>${nl}"
expected+="[2] qq: <'Monthly Report \$Q3.txt'>${nl}"
expected+="[2] q-: <'Monthly Report \$Q3.txt'>${nl}"
expected+="[2] rt: ok${nl}"
expected+="[3] raw: <it${sq}s>${nl}"
expected+="[3] q: <it\\${sq}s>${nl}"
expected+="[3] qq: <'it'\\''s'>${nl}"
expected+="[3] q-: <it\\${sq}s>${nl}"
expected+="[3] rt: ok${nl}"
expected+="[4] raw: <>${nl}"
expected+="[4] q: <''>${nl}"
expected+="[4] qq: <''>${nl}"
expected+="[4] q-: <''>${nl}"
expected+="[4] rt: ok${nl}"
expected+="[5] raw: <a${tab}b>${nl}"
expected+="[5] q: <a\$'\\t'b>${nl}"
expected+="[5] qq: <'a${tab}b'>${nl}"
expected+="[5] q-: <'a${tab}b'>${nl}"
expected+="[5] rt: ok${nl}"
expected+="[6] raw: <*.log>${nl}"
expected+="[6] q: <\\*.log>${nl}"
expected+="[6] qq: <'*.log'>${nl}"
expected+="[6] q-: <'*.log'>${nl}"
expected+="[6] rt: ok${nl}"
expected+="[7] raw: <back\\slash>${nl}"
expected+="[7] q: <back\\\\slash>${nl}"
expected+="[7] qq: <'back\\slash'>${nl}"
expected+="[7] q-: <'back\\slash'>${nl}"
expected+="[7] rt: ok${nl}"

assert_eq 'report mode: exit code' '0' "$RC"
assert_eq 'report mode: stderr is quiet' '' "$ERR"
assert_eq 'report mode: exact renderings' "$expected" "$OUT"

# ---- 2. report mode: a word containing a newline spans lines faithfully ------
run_tool "two${nl}lines"
expected="[1] raw: <two${nl}lines>${nl}"
expected+="[1] q: <two\$'\\n'lines>${nl}"
expected+="[1] qq: <'two${nl}lines'>${nl}"
expected+="[1] q-: <'two${nl}lines'>${nl}"
expected+="[1] rt: ok${nl}"
assert_eq 'newline word: exit code' '0' "$RC"
assert_eq 'newline word: exact renderings' "$expected" "$OUT"

# ---- 3. report mode with no words prints nothing ------------------------------
run_tool
assert_eq 'no words: exit code' '0' "$RC"
assert_eq 'no words: stdout empty' '' "$OUT"
assert_eq 'no words: stderr empty' '' "$ERR"

# ---- 4. --line: one copy-pasteable line in minimal quoting --------------------
run_tool --line tar -czf 'quarterly reports.tgz' "Monthly Report \$Q3.txt" '' "it${sq}s"
assert_eq 'line mode: exit code' '0' "$RC"
assert_eq 'line mode: stderr is quiet' '' "$ERR"
assert_eq 'line mode: rendering' \
  "tar -czf 'quarterly reports.tgz' 'Monthly Report \$Q3.txt' '' it\\${sq}s${nl}" "$OUT"

run_tool --line
assert_eq 'line mode, no words: prints an empty line' "${nl}" "$OUT"
assert_eq 'line mode, no words: exit code' '0' "$RC"

# the rendered line must eval back to the original argv, byte for byte
orig=(cp -- "Monthly Report \$Q3.txt" "it${sq}s" '' "a${tab}b" '*.log' 'C:\temp\new')
run_tool --line "${orig[@]}"
eval "back=(${OUT%$nl})"
(( checks += 1 ))
if [[ ${#back} -ne ${#orig} ]]; then
  (( fails += 1 ))
  printf 'FAIL line mode round trip: argc %d became %d\n' "${#orig}" "${#back}"
else
  typeset -i i
  for (( i = 1; i <= ${#orig}; i++ )); do
    if [[ "${back[$i]}" != "${orig[$i]}" ]]; then
      (( fails += 1 ))
      printf 'FAIL line mode round trip: arg %d changed\n--- sent ---\n%s\n--- got back ---\n%s\n' \
        "$i" "${orig[$i]}" "${back[$i]}"
      break
    fi
  done
fi

# ---- 5. --unquote: strip one level of any quoting form -------------------------
run_tool --unquote "Monthly\\ Report\\ \\\$Q3.txt" "'it'\\''s'" "a\$'\\t'b" "''"
expected="<Monthly Report \$Q3.txt>${nl}"
expected+="<it${sq}s>${nl}"
expected+="<a${tab}b>${nl}"
expected+="<>${nl}"
assert_eq 'unquote mode: exit code' '0' "$RC"
assert_eq 'unquote mode: recovered words' "$expected" "$OUT"

run_tool --unquote
assert_eq 'unquote mode, no words: stdout empty' '' "$OUT"
assert_eq 'unquote mode, no words: exit code' '0' "$RC"

# ---- 6. option validation -------------------------------------------------------
run_tool --wat report
assert_eq 'unknown option: exit code' '2' "$RC"
assert_eq 'unknown option: stdout empty' '' "$OUT"
assert_eq 'unknown option: stderr' \
  "usage: argshow.zsh [--line|--unquote] [word ...]${nl}" "$ERR"

# ---- summary --------------------------------------------------------------------
if (( fails > 0 )); then
  printf '%d/%d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'ok - %d checks passed\n' "$checks"
