#!/usr/bin/env bash
# Regression harness for offload.sh.
# Run from the workspace root:  bash test_offload.sh
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
    printf 'PASS %s\n' "$1"
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
  return 1
}

assert_same() { # assert_same <label> <file-a> <file-b> -- byte-identical files
  checks=$((checks + 1))
  if cmp -s "$2" "$3"; then
    printf 'PASS %s\n' "$1"
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (%s and %s differ or are missing)\n' "$1" "$2" "$3"
  return 1
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

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

if [[ ! -f offload.sh ]]; then
  printf 'FAIL offload.sh not found in the workspace root\n'
  exit 1
fi

# ---- card fixture: names straight off a field recorder ---------------------------

CARD="$T/card"
VAULT="$T/vault"
mkdir -p "$CARD"
printf 'fake wav: ambience take 2\n' > "$CARD/AMBI 0004 take 2.wav"
printf 'fake wav: intro take 1\n'    > "$CARD/intro take 1.wav"
printf 'fake wav: jingle\n'          > "$CARD/jingle.wav"
printf 'dash file, mix notes\n'      > "$CARD/-mixnotes.txt"
printf 'previous mix\n'              > "$CARD/old take.bak"
printf 'scratch buffer\n'            > "$CARD/scratch.tmp"
printf 'skip=tmp,bak\nlabels=morning,field,b-roll\n' > "$CARD/offload.conf"

# ---- offload run ------------------------------------------------------------------

printf -v exp_summary 'offloaded: -mixnotes.txt AMBI 0004 take 2.wav intro take 1.wav jingle.wav (4 files, 87 bytes)\n'

run_in "$T" bash "$ROOT/offload.sh" card vault
expect "offload run" 0 "$exp_summary" ""

# vault holds exactly the four clips plus the index — no fragments, no extras
printf -v exp_ls -- '-mixnotes.txt\nAMBI 0004 take 2.wav\nindex.txt\nintro take 1.wav\njingle.wav'
assert_eq "vault listing" "$exp_ls" "$( cd "$VAULT" && ls -A )"

assert_same "spacey clip 1 arrived intact" "$CARD/AMBI 0004 take 2.wav" "$VAULT/AMBI 0004 take 2.wav"
assert_same "spacey clip 2 arrived intact" "$CARD/intro take 1.wav" "$VAULT/intro take 1.wav"
assert_same "plain clip arrived intact" "$CARD/jingle.wav" "$VAULT/jingle.wav"
assert_same "dash-named sidecar arrived intact" "$CARD/-mixnotes.txt" "$VAULT/-mixnotes.txt"

INDEX=''
if [[ -f "$VAULT/index.txt" ]]; then
  slurp INDEX "$VAULT/index.txt"
fi
printf -v exp_index 'labels: morning field b-roll\n-mixnotes.txt\t21\nAMBI 0004 take 2.wav\t26\nintro take 1.wav\t23\njingle.wav\t17\ntotal\t87\n'
assert_eq "index.txt records every clip with true sizes" "$exp_index" "$INDEX"

# skip extensions and the conf itself stay on the card
checks=$((checks + 1))
if [[ ! -e "$VAULT/scratch.tmp" && ! -e "$VAULT/old take.bak" && ! -e "$VAULT/offload.conf" ]]; then
  printf 'PASS skip list and conf are not offloaded\n'
else
  fails=$((fails + 1))
  printf 'FAIL skip list and conf are not offloaded\n'
fi

# ---- second run: same card, same result (re-offload after a reshoot) --------------

run_in "$T" bash "$ROOT/offload.sh" card vault
expect "second offload run is identical" 0 "$exp_summary" ""
INDEX=''
slurp INDEX "$VAULT/index.txt"
assert_eq "index.txt stable across runs" "$exp_index" "$INDEX"

# ---- error handling ----------------------------------------------------------------

CARD2="$T/card2"
mkdir -p "$CARD2"
printf 'fake wav: solo\n' > "$CARD2/solo.wav"
printf -v exp_noconf 'offload.sh: missing offload.conf in card2\n'
run_in "$T" bash "$ROOT/offload.sh" card2 vault2
expect "card without offload.conf" 66 "" "$exp_noconf"

printf -v exp_nodir 'offload.sh: not a directory: ghostcard\n'
run_in "$T" bash "$ROOT/offload.sh" ghostcard vault2
expect "missing card directory" 66 "" "$exp_nodir"

printf -v exp_usage 'usage: offload.sh <carddir> <vaultdir>\n'
run_in "$T" bash "$ROOT/offload.sh" card
expect "missing vault argument" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/offload.sh" card vault extra
expect "extra argument" 64 "" "$exp_usage"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf 'SUMMARY: %d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'SUMMARY: all %d checks passed\n' "$checks"
