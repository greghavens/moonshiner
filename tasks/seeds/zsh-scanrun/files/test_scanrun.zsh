#!/usr/bin/env zsh
# Acceptance harness for collect_run.zsh (scan-bench run collector).
# Run from the workspace root:  zsh test_scanrun.zsh
emulate -R zsh
setopt no_unset
LC_ALL=C
export LC_ALL
unset CDPATH cdpath 2>/dev/null

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- ${0:h}

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

assert_absent() { # assert_absent <label> <path>
  (( checks += 1 ))
  if [[ ! -e "$2" ]]; then
    return 0
  fi
  (( fails += 1 ))
  printf 'FAIL %s: %s exists but must not\n' "$1" "$2"
}

slurp() { # slurp <var> <file> -- byte-exact contents (empty if absent)
  local __v=$1 __f=$2
  if [[ -f $__f ]]; then
    typeset -g "$__v"="$(<$__f)"
  else
    typeset -g "$__v"=""
  fi
}

typeset -g RC OUT ERR
run_in() { # run_in <dir> <cmd...> -- capture RC, OUT, ERR
  local d=$1
  shift
  ( cd "$d" && "$@" ) > "$ROOT/$T/out" 2> "$ROOT/$T/err"
  RC=$?
  slurp OUT "$ROOT/$T/out"
  slurp ERR "$ROOT/$T/err"
}

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

if [[ ! -f collect_run.zsh ]]; then
  printf 'FAIL collect_run.zsh not found in the workspace root\n'
  exit 1
fi

# ---- the script must parse: zsh -n is part of the gate -------------------------

zsh -n collect_run.zsh > "$T/nout" 2> "$T/nerr"
nrc=$?
slurp NERR "$T/nerr"
assert_eq "zsh -n collect_run.zsh: exit code" 0 "$nrc"
assert_eq "zsh -n collect_run.zsh: stderr" "" "$NERR"

# ---- fixtures -------------------------------------------------------------------

mkdir -p "$T/bench/color/rejects" "$T/bench/mono"
print -n 'frame one, colour pass'  > "$T/bench/color/frame01.png"
print -n 'frame two, colour pass'  > "$T/bench/color/frame02.png"
print -n 'frame three, mono pass'  > "$T/bench/mono/frame03.png"
print -n 'smudged, do not collect' > "$T/bench/color/rejects/frame00.png"
print -n 'bench notes, not a frame' > "$T/bench/readme.txt"

mkdir -p "$T/quietbench/color" "$T/quietbench/mono"
print -n 'lone colour frame' > "$T/quietbench/color/frame09.png"

mkdir -p "$T/emptybench/color" "$T/emptybench/mono"

# ---- argument handling -------------------------------------------------------------

run_in "$T" zsh "$ROOT/collect_run.zsh"
expect "no arguments" 64 "" "usage: collect_run.zsh <bench-dir> <run-dir>"

run_in "$T" zsh "$ROOT/collect_run.zsh" bench
expect "one argument" 64 "" "usage: collect_run.zsh <bench-dir> <run-dir>"

mkdir -p "$T/notabench"
run_in "$T" zsh "$ROOT/collect_run.zsh" notabench run_x
expect "not a bench" 66 "" "collect_run.zsh: not a scan bench: notabench"
assert_absent "not a bench: no run contents" "$T/run_x/index.txt"

# ---- a fresh collect: plain files from both trays, nothing else --------------------

run_in "$T" zsh "$ROOT/collect_run.zsh" bench run1
expect "fresh collect" 0 "collected: 3 frame(s)" ""

slurp IDX "$T/run1/index.txt"
assert_eq "fresh collect: index.txt" \
"color__frame01.png
color__frame02.png
mono__frame03.png" "$IDX"

slurp F1 "$T/run1/color__frame01.png"
assert_eq "fresh collect: frame01 bytes" 'frame one, colour pass' "$F1"
slurp F3 "$T/run1/mono__frame03.png"
assert_eq "fresh collect: frame03 bytes" 'frame three, mono pass' "$F3"
assert_absent "rejects subfolder stays behind" "$T/run1/rejects__frame00.png"
assert_absent "rejects subfolder stays behind (any name)" "$T/run1/color__rejects"
assert_absent "bench-level stray file stays behind" "$T/run1/readme.txt"

# ---- collecting the same run twice refuses -----------------------------------------

run_in "$T" zsh "$ROOT/collect_run.zsh" bench run1
expect "second collect refuses" 65 "" "collect_run.zsh: already collected: run1"

# ---- a bench with an empty mono tray still collects the colour tray ----------------

run_in "$T" zsh "$ROOT/collect_run.zsh" quietbench run2
expect "empty mono tray" 0 "collected: 1 frame(s)" ""
slurp IDX2 "$T/run2/index.txt"
assert_eq "empty mono tray: index.txt" "color__frame09.png" "$IDX2"

# ---- leftovers from a crashed collect are refused ----------------------------------

mkdir -p "$T/run3"
print -n 'half-written batch list' > "$T/run3/part_3.lst"
run_in "$T" zsh "$ROOT/collect_run.zsh" bench run3
expect "partial batch refused" 65 "" "collect_run.zsh: partial batch in run3, sweep it first"
assert_absent "partial batch: nothing copied" "$T/run3/index.txt"

# ---- an entirely empty bench collects nothing --------------------------------------

run_in "$T" zsh "$ROOT/collect_run.zsh" emptybench run4
expect "empty bench" 1 "" "collect_run.zsh: nothing to collect"
assert_absent "empty bench: no index written" "$T/run4/index.txt"

# ---- summary ------------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
