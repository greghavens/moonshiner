#!/usr/bin/env bash
# Acceptance harness for maintlog.sh.
# Run from the workspace root:  bash test_maintlog.sh
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

note_fail() {
  fails=$((fails + 1))
  printf 'FAIL %s\n' "$1"
}

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

if [[ ! -f maintlog.sh ]]; then
  printf 'FAIL maintlog.sh not found in the workspace root\n'
  exit 1
fi

W="$T/work"
mkdir -p "$W"
mrun() { run_in "$W" bash "$ROOT/maintlog.sh" "$@"; }

# The usage block, byte-exact (also printed after every usage error).
cat > "$T/usage" <<'EOF'
usage: maintlog.sh <command> [options]

commands:
  init                                  create an empty log in this directory
  add -H <host> -m <text> [-t <tag>]    append a maintenance entry
  report [-H <host>] [-n <count>]       print entries, oldest first

long options: --host=<v>  --message=<v>  --tag=<v>  --limit=<v>  --help
exit codes:   0 ok, 64 usage error, 65 data error
EOF
slurp USAGE "$T/usage"

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

nl=$'\n'

# ---- before init -----------------------------------------------------------

mrun
expect "no arguments" 64 "" "maintlog: missing command$nl$USAGE"

mrun -h
expect "-h alone" 0 "$USAGE" ""

mrun --help
expect "--help alone" 0 "$USAGE" ""

mrun frob
expect "unknown command" 64 "" "maintlog: unknown command: frob$nl$USAGE"

mrun add -H web1 -m "rotated logs"
expect "add before init" 65 "" "maintlog: not initialized (run 'maintlog.sh init' first)$nl"

mrun report
expect "report before init" 65 "" "maintlog: not initialized (run 'maintlog.sh init' first)$nl"

# ---- init ------------------------------------------------------------------

mrun init
expect "init" 0 "initialized .maintlog$nl" ""
[[ -f "$W/.maintlog" ]]; assert_true "init creates .maintlog" "$?"
[[ ! -s "$W/.maintlog" ]]; assert_true "fresh log is empty" "$?"

mrun init
expect "init twice" 65 "" "maintlog: already initialized$nl"

mrun report
expect "report on empty log" 0 "" ""

# ---- add -------------------------------------------------------------------

mrun add -H web1 -m "rotated logs"
expect "add short options" 0 "added #1$nl" ""

mrun add --host=web2 --message=reboot --tag=power
expect "add long options" 0 "added #2$nl" ""

mrun add -t disk -m "swapped /dev/sdb after SMART warnings" -H web1
expect "add options in any order" 0 "added #3$nl" ""

mrun add -H web3 -m "- verify crown backup ring"
expect "add message starting with a dash" 0 "added #4$nl" ""

mrun add -m "no host given"
expect "add missing -H" 64 "" "maintlog: add: -H and -m are required$nl$USAGE"

mrun add -H web1 -m ""
expect "add empty -m counts as missing" 64 "" "maintlog: add: -H and -m are required$nl$USAGE"

mrun add -H web1 -m ok -x
expect "add unknown short option" 64 "" "maintlog: unknown option: -x$nl$USAGE"

mrun add -H web1 -m
expect "add option missing its value" 64 "" "maintlog: option -m requires a value$nl$USAGE"

mrun add --retries=3 -H web1 -m ok
expect "add unknown long option" 64 "" "maintlog: unknown option: --retries=3$nl$USAGE"

mrun add -H web1 -m ok stray
expect "add trailing junk" 64 "" "maintlog: add: unexpected argument: stray$nl$USAGE"

mrun add -H web1 -m $'bad\tfield'
expect "add rejects tab in a field" 65 "" "maintlog: fields may not contain tabs$nl"

mrun add -h
expect "add -h" 0 "$USAGE" ""

# ---- report ----------------------------------------------------------------

printf -v exp_all '#1\tweb1\t-\trotated logs\n#2\tweb2\tpower\treboot\n#3\tweb1\tdisk\tswapped /dev/sdb after SMART warnings\n#4\tweb3\t-\t- verify crown backup ring\n'
mrun report
expect "report all entries" 0 "$exp_all" ""

printf -v exp_web1 '#1\tweb1\t-\trotated logs\n#3\tweb1\tdisk\tswapped /dev/sdb after SMART warnings\n'
mrun report -H web1
expect "report filtered by host" 0 "$exp_web1" ""

printf -v exp_last2 '#3\tweb1\tdisk\tswapped /dev/sdb after SMART warnings\n#4\tweb3\t-\t- verify crown backup ring\n'
mrun report -n 2
expect "report last two" 0 "$exp_last2" ""

printf -v exp_combo '#3\tweb1\tdisk\tswapped /dev/sdb after SMART warnings\n'
mrun report --host=web1 --limit=1
expect "report long options combine" 0 "$exp_combo" ""

mrun report -n 99
expect "report -n larger than log" 0 "$exp_all" ""

mrun report -n 0
expect "report count zero" 64 "" "maintlog: invalid count: 0$nl$USAGE"

mrun report -n plenty
expect "report count not a number" 64 "" "maintlog: invalid count: plenty$nl$USAGE"

mrun report -H nosuch
expect "report filter with no matches" 0 "" ""

# ---- summary ---------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
