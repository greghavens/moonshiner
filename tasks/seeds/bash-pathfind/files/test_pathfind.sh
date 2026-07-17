#!/usr/bin/env bash
# Acceptance harness for pathfind.sh (which-clone that walks $PATH itself).
# Run from the workspace root:  bash test_pathfind.sh
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

if [[ ! -f pathfind.sh ]]; then
  printf 'FAIL pathfind.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

nl=$'\n'

# ---- fixtures: a small forest of lookup directories ---------------------------

A="$ROOT/$T/bin_a"
B="$ROOT/$T/bin_b"
C="$ROOT/$T/bin_c"   # tool present but not executable
D="$ROOT/$T/bin_d"   # tool is a directory
E="$ROOT/$T/bin_e"   # unrelated command only
DECOY="$ROOT/$T/decoy"
mkdir -p "$A" "$B" "$C" "$D/tool" "$E" "$DECOY" "$ROOT/$T/cwd" "$ROOT/$T/cwd2"

printf '#!/bin/sh\necho a\n' > "$A/tool"
printf '#!/bin/sh\necho b\n' > "$B/tool"
printf '#!/bin/sh\necho c\n' > "$C/tool"
printf '#!/bin/sh\necho e\n' > "$E/other"
printf '#!/bin/sh\necho decoy\n' > "$DECOY/tool"
printf '#!/bin/sh\necho fake echo\n' > "$A/echo"
printf '#!/bin/sh\necho local\n' > "$ROOT/$T/cwd/tool"
printf '#!/bin/sh\necho only here\n' > "$ROOT/$T/cwd/local_only"
printf '#!/bin/sh\necho not runnable\n' > "$ROOT/$T/cwd2/tool"
chmod +x "$A/tool" "$B/tool" "$E/other" "$DECOY/tool" "$A/echo" \
         "$ROOT/$T/cwd/tool" "$ROOT/$T/cwd/local_only"
chmod -x "$C/tool" "$ROOT/$T/cwd2/tool"

printf -v exp_usage 'usage: pathfind.sh [-a] <name>\n'

# ---- argument validation -------------------------------------------------------

run_in "$T" env PATH="$A" "$BASH" "$ROOT/pathfind.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T" env PATH="$A" "$BASH" "$ROOT/pathfind.sh" tool extra
expect "two names" 64 "" "$exp_usage"

run_in "$T" env PATH="$A" "$BASH" "$ROOT/pathfind.sh" -x tool
expect "unknown option" 64 "" "$exp_usage"

run_in "$T" env PATH="$A" "$BASH" "$ROOT/pathfind.sh" -a
expect "-a without a name" 64 "" "$exp_usage"

run_in "$T" env PATH="$A" "$BASH" "$ROOT/pathfind.sh" ""
expect "empty name" 64 "" "$exp_usage"

run_in "$T" env PATH="$A" "$BASH" "$ROOT/pathfind.sh" sub/tool
expect "name with slash" 64 "" "pathfind.sh: name must not contain '/'$nl"

# ---- precedence and filtering ----------------------------------------------------

run_in "$T" env PATH="$A:$B" "$BASH" "$ROOT/pathfind.sh" tool
expect "first match wins" 0 "$A/tool$nl" ""

run_in "$T" env PATH="$B:$A" "$BASH" "$ROOT/pathfind.sh" tool
expect "precedence follows PATH order" 0 "$B/tool$nl" ""

run_in "$T" env PATH="$C:$B" "$BASH" "$ROOT/pathfind.sh" tool
expect "non-executable file is skipped" 0 "$B/tool$nl" ""

run_in "$T" env PATH="$D:$B" "$BASH" "$ROOT/pathfind.sh" tool
expect "directory named like the command is skipped" 0 "$B/tool$nl" ""

run_in "$T" env PATH="$E" "$BASH" "$ROOT/pathfind.sh" tool
expect "no match anywhere" 1 "" "pathfind.sh: tool: not found in PATH$nl"

run_in "$T" env PATH="$C" "$BASH" "$ROOT/pathfind.sh" tool
expect "only a non-executable candidate" 1 "" "pathfind.sh: tool: not found in PATH$nl"

# ---- -a lists every match in precedence order --------------------------------------

run_in "$T" env PATH="$A:$C:$D:$B:$A" "$BASH" "$ROOT/pathfind.sh" -a tool
expect "-a lists all matches, duplicates kept" 0 "$A/tool$nl$B/tool$nl$A/tool$nl" ""

run_in "$T" env PATH="$B" "$BASH" "$ROOT/pathfind.sh" -a tool
expect "-a with a single match" 0 "$B/tool$nl" ""

run_in "$T" env PATH="$E" "$BASH" "$ROOT/pathfind.sh" -a tool
expect "-a with no match" 1 "" "pathfind.sh: tool: not found in PATH$nl"

# ---- empty PATH segments mean the current directory --------------------------------

run_in "$T/cwd" env PATH=":$E" "$BASH" "$ROOT/pathfind.sh" tool
expect "leading empty segment searches cwd" 0 "./tool$nl" ""

run_in "$T/cwd" env PATH="$E:" "$BASH" "$ROOT/pathfind.sh" tool
expect "trailing empty segment searches cwd" 0 "./tool$nl" ""

run_in "$T/cwd" env PATH="$A::$B" "$BASH" "$ROOT/pathfind.sh" local_only
expect "double-colon segment searches cwd" 0 "./local_only$nl" ""

run_in "$T/cwd" env PATH="$A::$B" "$BASH" "$ROOT/pathfind.sh" tool
expect "real dir still beats a later empty segment" 0 "$A/tool$nl" ""

run_in "$T/cwd2" env PATH=":$B" "$BASH" "$ROOT/pathfind.sh" tool
expect "non-executable file in cwd is skipped" 0 "$B/tool$nl" ""

run_in "$T/cwd" env PATH= "$BASH" "$ROOT/pathfind.sh" tool
expect "empty PATH is one cwd segment" 0 "./tool$nl" ""

# a shell can only see PATH truly unset when the resolver is sourced into it
run_in "$T/cwd" "$BASH" -c "unset PATH; set -- tool; . $ROOT/pathfind.sh"
expect "unset PATH behaves like empty PATH" 0 "./tool$nl" ""

# ---- the resolver must not lean on the shell's own lookup machinery ----------------

# a name that is also a shell builtin: the only honest answer comes from $PATH
run_in "$T" env PATH="$E" "$BASH" "$ROOT/pathfind.sh" echo
expect "builtin name, absent from PATH" 1 "" "pathfind.sh: echo: not found in PATH$nl"

run_in "$T" env PATH="$A:$E" "$BASH" "$ROOT/pathfind.sh" echo
expect "builtin name, present in PATH" 0 "$A/echo$nl" ""

# sourced into a shell whose hash table remembers an out-of-date location
run_in "$T" env PATH="$A:$B" "$BASH" -c "hash -p $DECOY/tool tool; set -- tool; . $ROOT/pathfind.sh"
expect "sourced: stale hash entry is ignored" 0 "$A/tool$nl" ""

run_in "$T" env PATH="$A:$B" "$BASH" -c "hash -p $DECOY/tool tool; set -- -a tool; . $ROOT/pathfind.sh"
expect "sourced -a: stale hash entry is ignored" 0 "$A/tool$nl$B/tool$nl" ""

run_in "$T" env PATH="$E" "$BASH" -c "hash -p $DECOY/tool tool; set -- tool; . $ROOT/pathfind.sh"
expect "sourced: hashed-only command is not a PATH match" 1 "" "pathfind.sh: tool: not found in PATH$nl"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
