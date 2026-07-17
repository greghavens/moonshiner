#!/usr/bin/env bash
# Acceptance harness for inv.sh.
# Run from the workspace root:  bash test_inv.sh
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

if [[ ! -f inv.sh ]]; then
  printf 'FAIL inv.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

# ---- fixtures ---------------------------------------------------------------

# Main feed: 16 physical lines; the line numbers matter for the warnings.
# Assets carry awkward real-world label text: commas, doubled/leading spaces,
# a leading dash, glob-looking brackets and stars, UTF-8.
printf '# nightly scan, floor 2\nlabel printer\tfront desk\npower strip, 8-way\track A7\n\nlabel printer\tfront desk\ncable [cat6] *spare*\track A7\nspare  fan\track A7\n-r drive caddy\track B2\nlabel printer\track B2\nno tab on this line\ntwo\ttabs\there\n\torphan location\nghost asset\t\nlabel printer\tfront desk\n  indented asset\track B2\ncaf\303\251 badge\tfront desk\n' > "$T/feed.txt"

# Decoy files in the tool's working directory: if a report row were expanded
# unquoted, the glob-looking asset names above would match these names and the
# byte-exact comparisons below would catch the mangled row.
: > "$T/c"
: > "$T/old-spare-fan"

# Second feed: several repeated pairs, to pin the REPEATED section ordering.
printf 'usb hub\tdesk 3\nusb hub\tdesk 3\nmouse\tdesk 1\nmouse\tdesk 1\nmouse\tdesk 2\nmouse\tdesk 2\nusb hub\tdesk 1\nusb hub\tdesk 1\n' > "$T/feed2.txt"

# Third feed: nothing but comments and blank lines.
printf '# empty scan\n\n# nothing checked in today\n\n' > "$T/feed3.txt"

# ---- main feed --------------------------------------------------------------

printf -v exp_main_out 'BY-LOCATION\nfront desk\t2\t4\nrack A7\t3\t3\nrack B2\t3\t3\nREPEATED\nlabel printer\tfront desk\t3\nTOTALS\nlocations\t3\nassets\t7\nscans\t10\nrepeated\t1\n'
printf -v exp_main_err 'inv.sh: line 10: malformed, skipped\ninv.sh: line 11: malformed, skipped\ninv.sh: line 12: malformed, skipped\ninv.sh: line 13: malformed, skipped\n'

run_in "$T" bash "$ROOT/inv.sh" feed.txt
expect "main feed" 0 "$exp_main_out" "$exp_main_err"
first_out=$OUT

run_in "$T" bash "$ROOT/inv.sh" feed.txt
expect "main feed, second run" 0 "$exp_main_out" "$exp_main_err"
assert_eq "report is byte-stable across runs" "$first_out" "$OUT"

# ---- repeated-section ordering ----------------------------------------------

printf -v exp_rep_out 'BY-LOCATION\ndesk 1\t2\t4\ndesk 2\t1\t2\ndesk 3\t1\t2\nREPEATED\nmouse\tdesk 1\t2\nmouse\tdesk 2\t2\nusb hub\tdesk 1\t2\nusb hub\tdesk 3\t2\nTOTALS\nlocations\t3\nassets\t2\nscans\t8\nrepeated\t4\n'

run_in "$T" bash "$ROOT/inv.sh" feed2.txt
expect "repeated pairs ordering" 0 "$exp_rep_out" ""

# ---- empty feed ---------------------------------------------------------------

printf -v exp_empty_out 'BY-LOCATION\nREPEATED\nTOTALS\nlocations\t0\nassets\t0\nscans\t0\nrepeated\t0\n'

run_in "$T" bash "$ROOT/inv.sh" feed3.txt
expect "comments-and-blanks-only feed" 0 "$exp_empty_out" ""

# ---- argument errors ----------------------------------------------------------

printf -v exp_usage_err 'usage: inv.sh <feed-file>\n'
run_in "$T" bash "$ROOT/inv.sh"
expect "no arguments" 64 "" "$exp_usage_err"

printf -v exp_noread_err 'inv.sh: cannot read: nope.txt\n'
run_in "$T" bash "$ROOT/inv.sh" nope.txt
expect "unreadable feed file" 66 "" "$exp_noread_err"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
