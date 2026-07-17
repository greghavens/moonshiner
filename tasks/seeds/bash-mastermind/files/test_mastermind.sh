#!/usr/bin/env bash
# Acceptance harness for mastermind.sh.
# Run from the workspace root:  bash test_mastermind.sh
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- "${0%/*}"

if [[ ! -f ./mastermind.sh ]]; then
  printf 'FAIL mastermind.sh not found in the workspace root\n'
  exit 1
fi

checks=0
fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  checks=$((checks + 1))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n---------------\n' "$1" "$2" "$3"
}

run() { "$BASH" ./mastermind.sh "$@"; }

rm -rf fixtures
mkdir fixtures

# ---- score subcommand: the peg law, duplicates included ----------------------

score_case() { # score_case <secret> <guess> <expected>
  local out st
  out=$(run score "$1" "$2"); st=$?
  assert_eq "score $1 vs $2" "$3" "$out"
  assert_eq "score $1 vs $2 exit status" 0 "$st"
}

score_case 1234 1234 'black=4 white=0'
score_case 6666 6666 'black=4 white=0'
score_case 1234 4321 'black=0 white=4'
score_case 1234 1243 'black=2 white=2'
score_case 1234 2134 'black=2 white=2'
score_case 1234 5656 'black=0 white=0'
score_case 1234 1111 'black=1 white=0'
score_case 1234 1555 'black=1 white=0'
score_case 1122 2211 'black=0 white=4'
score_case 1122 1212 'black=2 white=2'
score_case 1122 1111 'black=2 white=0'
score_case 1223 2213 'black=2 white=2'
score_case 5511 5151 'black=2 white=2'
score_case 1112 1211 'black=2 white=2'
score_case 2245 4522 'black=0 white=4'

err_case() { # err_case <label> <expected-stderr> [arg]...
  local label=$1 expected=$2
  shift 2
  local out st err
  out=$(run "$@" 2>fixtures/.stderr </dev/null); st=$?
  err=$(<fixtures/.stderr)
  assert_eq "$label: exit status" 2 "$st"
  assert_eq "$label: stderr" "$expected" "$err"
  assert_eq "$label: stdout stays empty" '' "$out"
}

code_err='error: code must be 4 digits 1-6'
err_case 'score: secret too short' "$code_err" score 123 1234
err_case 'score: secret too long' "$code_err" score 12345 1234
err_case 'score: secret digit out of range' "$code_err" score 1237 1234
err_case 'score: guess not digits' "$code_err" score 1234 abcd
err_case 'score: guess with a zero' "$code_err" score 1234 1204

usage='usage: mastermind.sh score <secret> <guess> | play <secret-file>'
printf '1234\n' > fixtures/secret.txt
err_case 'no arguments' "$usage"
err_case 'unknown subcommand' "$usage" deal 1234 5678
err_case 'score: missing guess' "$usage" score 1234
err_case 'play: extra argument' "$usage" play fixtures/secret.txt extra

# ---- play subcommand ----------------------------------------------------------

play_case() { # play_case <label> <secret> <expected-status> <expected> <guesses...>
  local label=$1 secret=$2 want_st=$3 expected=$4
  shift 4
  printf '%s\n' "$secret" > fixtures/secret.txt
  local out st
  out=$(printf '%s\n' "$@" | run play fixtures/secret.txt); st=$?
  assert_eq "$label" "$expected" "$out"
  assert_eq "$label: exit status" "$want_st" "$st"
}

expected=$(cat <<'EOF'
turn 1/10: 1122
  1: 1122  W---
turn 2/10: 3344
  1: 1122  W---
  2: 3344  BW--
turn 3/10: 3416
  1: 1122  W---
  2: 3344  BW--
  3: 3416  BBBB
cracked in 3 guesses
EOF
)
play_case 'play: three-turn crack with growing history' 3416 0 "$expected" \
  1122 3344 3416

expected=$(cat <<'EOF'
invalid guess: 12345
turn 1/10: 1255
  1: 1255  BWW-
invalid guess: 0164
turn 2/10: 2545
  1: 1255  BWW-
  2: 2545  BBWW
turn 3/10: 2554
  1: 1255  BWW-
  2: 2545  BBWW
  3: 2554  BBBB
cracked in 3 guesses
EOF
)
play_case 'play: malformed guesses are called out and cost no turn' 2554 0 "$expected" \
  12345 '' 1255 0164 2545 2554

expected=$(cat <<'EOF'
turn 1/10: 4444
  1: 4444  BBBB
cracked in 1 guess
EOF
)
play_case 'play: first-guess crack uses the singular' 4444 0 "$expected" 4444

expected=$(cat <<'EOF'
turn 1/10: 1111
  1: 1111  B---
turn 2/10: 2222
  1: 1111  B---
  2: 2222  B---
turn 3/10: 3333
  1: 1111  B---
  2: 2222  B---
  3: 3333  B---
turn 4/10: 4444
  1: 1111  B---
  2: 2222  B---
  3: 3333  B---
  4: 4444  B---
turn 5/10: 5555
  1: 1111  B---
  2: 2222  B---
  3: 3333  B---
  4: 4444  B---
  5: 5555  ----
turn 6/10: 6666
  1: 1111  B---
  2: 2222  B---
  3: 3333  B---
  4: 4444  B---
  5: 5555  ----
  6: 6666  ----
turn 7/10: 1122
  1: 1111  B---
  2: 2222  B---
  3: 3333  B---
  4: 4444  B---
  5: 5555  ----
  6: 6666  ----
  7: 1122  BW--
turn 8/10: 2211
  1: 1111  B---
  2: 2222  B---
  3: 3333  B---
  4: 4444  B---
  5: 5555  ----
  6: 6666  ----
  7: 1122  BW--
  8: 2211  BW--
turn 9/10: 3344
  1: 1111  B---
  2: 2222  B---
  3: 3333  B---
  4: 4444  B---
  5: 5555  ----
  6: 6666  ----
  7: 1122  BW--
  8: 2211  BW--
  9: 3344  BW--
turn 10/10: 4433
  1: 1111  B---
  2: 2222  B---
  3: 3333  B---
  4: 4444  B---
  5: 5555  ----
  6: 6666  ----
  7: 1122  BW--
  8: 2211  BW--
  9: 3344  BW--
  10: 4433  BW--
out of guesses -- the code was 1234
EOF
)
play_case 'play: ten wrong guesses reveal the code' 1234 1 "$expected" \
  1111 2222 3333 4444 5555 6666 1122 2211 3344 4433

expected=$(cat <<'EOF'
turn 1/10: 1111
  1: 1111  B---
no more input -- the code was 1234
EOF
)
play_case 'play: input drying up reveals the code' 1234 1 "$expected" 1111

# secret file problems
printf '12\n' > fixtures/short.txt
err_case 'play: malformed secret file' 'error: bad secret file' play fixtures/short.txt
printf '1260\n' > fixtures/zero.txt
err_case 'play: secret digit out of range' 'error: bad secret file' play fixtures/zero.txt
err_case 'play: missing secret file' 'error: cannot read fixtures/nope.txt' play fixtures/nope.txt

# ---- summary -----------------------------------------------------------------

printf '%d checks, %d failures\n' "$checks" "$fails"
if (( fails > 0 )); then
  exit 1
fi
printf 'OK\n'
