#!/usr/bin/env bash
# Acceptance harness for yahtzee.sh (scoring engine + scripted game runner).
# Run from the workspace root:  bash test_yahtzee.sh
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- "${0%/*}"

if [[ ! -f ./yahtzee.sh ]]; then
  printf 'FAIL yahtzee.sh not found in the workspace root\n'
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

run() { "$BASH" ./yahtzee.sh "$@" </dev/null; }

rm -rf fixtures
mkdir fixtures

# ---- score subcommand ---------------------------------------------------------

score_case() { # score_case <expected> <category> <d1..d5>
  local expected=$1
  shift
  local out st
  out=$(run score "$@"); st=$?
  assert_eq "score $*" "$expected" "$out"
  assert_eq "score $*: exit status" 0 "$st"
}

score_case 0 ones 2 3 4 5 6
score_case 3 ones 1 4 1 6 1
score_case 8 twos 2 2 3 2 2
score_case 30 sixes 6 6 6 6 6
score_case 12 three_of_a_kind 3 3 3 2 1
score_case 18 three_of_a_kind 4 4 4 4 2
score_case 25 three_of_a_kind 5 5 5 5 5
score_case 0 three_of_a_kind 2 2 3 3 6
score_case 18 four_of_a_kind 4 4 4 4 2
score_case 30 four_of_a_kind 6 6 6 6 6
score_case 0 four_of_a_kind 3 3 3 2 2
score_case 25 full_house 3 3 2 2 2
score_case 25 full_house 2 6 2 6 6
score_case 0 full_house 4 4 4 4 4
score_case 0 full_house 3 3 3 3 2
score_case 0 full_house 1 1 2 2 3
score_case 30 small_straight 1 2 3 4 4
score_case 30 small_straight 2 3 4 5 2
score_case 30 small_straight 3 4 5 6 6
score_case 30 small_straight 1 2 3 4 5
score_case 30 small_straight 4 2 1 3 6
score_case 0 small_straight 1 2 2 4 5
score_case 0 small_straight 1 1 3 4 5
score_case 40 large_straight 1 2 3 4 5
score_case 40 large_straight 2 3 4 5 6
score_case 40 large_straight 5 3 2 4 6
score_case 0 large_straight 1 2 3 4 4
score_case 50 yahtzee 2 2 2 2 2
score_case 0 yahtzee 2 2 2 2 3
score_case 16 chance 1 2 3 4 6
score_case 30 chance 6 6 6 6 6

err_case() { # err_case <label> <expected-stderr> [arg]...
  local label=$1 expected=$2
  shift 2
  local out st err
  out=$(run "$@" 2>fixtures/.stderr); st=$?
  err=$(<fixtures/.stderr)
  assert_eq "$label: exit status" 2 "$st"
  assert_eq "$label: stderr" "$expected" "$err"
  assert_eq "$label: stdout stays empty" '' "$out"
}

dice_err='error: dice must be five values 1-6'
err_case 'score: four dice' "$dice_err" score chance 1 2 3 4
err_case 'score: six dice' "$dice_err" score chance 1 2 3 4 5 6
err_case 'score: die of zero' "$dice_err" score chance 1 2 3 4 0
err_case 'score: die of seven' "$dice_err" score chance 1 2 3 4 7
err_case 'score: non-numeric die' "$dice_err" score chance 1 2 3 4 x
err_case 'score: unknown category' 'error: unknown category: flush' score flush 1 2 3 4 5

usage='usage: yahtzee.sh score <category> <d1> <d2> <d3> <d4> <d5> | play <rolls-file> <script-file>'
err_case 'no arguments' "$usage"
err_case 'unknown subcommand' "$usage" deal a b
err_case 'play: missing script file arg' "$usage" play fixtures/rollsA.txt
err_case 'play: extra argument' "$usage" play a b c

# ---- play subcommand: full games ------------------------------------------------

play_case() { # play_case <label> <expected-status> <expected> <rolls> <script>
  local label=$1 want_st=$2 expected=$3 rolls=$4 script=$5
  local out st
  out=$(run play "$rolls" "$script"); st=$?
  assert_eq "$label" "$expected" "$out"
  assert_eq "$label: exit status" "$want_st" "$st"
}

play_err() { # play_err <label> <expected-stdout> <expected-stderr> <rolls> <script>
  local label=$1 want_out=$2 want_err=$3 rolls=$4 script=$5
  local out st err
  out=$(run play "$rolls" "$script" 2>fixtures/.stderr); st=$?
  err=$(<fixtures/.stderr)
  assert_eq "$label: exit status" 2 "$st"
  assert_eq "$label: stderr" "$want_err" "$err"
  assert_eq "$label: transcript up to the failure" "$want_out" "$out"
}

printf '%s\n' 3 3 3 2 1 3 6 6 6 5 5 6 2 3 4 5 1 2 3 4 4 6 5 1 1 4 4 4 6 4 6 6 6 1 2 6 5 \
  5 5 5 2 2 4 4 1 4 6 4 2 2 2 6 1 3 2 2 5 1 1 1 3 5 1 5 4 6 3 3 2 4 6 2 5 1 2 3 4 2 \
  > fixtures/rollsA.txt
cat > fixtures/scriptA.txt <<'EOF'
# turn 1: chase threes
reroll 4 5
score threes
score full_house
score large_straight
reroll 5
score small_straight
reroll 1 2
score four_of_a_kind
reroll 4 5
score sixes
score fives
reroll 3 5
score fours

reroll 3 4
reroll 5
score twos
reroll 4
score ones
score chance
score three_of_a_kind
score yahtzee
EOF
expected=$(cat <<'EOF'
turn 1: roll 3 3 3 2 1
  reroll 4 5 -> 3 3 3 3 6
  score threes = 12
turn 2: roll 6 6 5 5 6
  score full_house = 25
turn 3: roll 2 3 4 5 1
  score large_straight = 40
turn 4: roll 2 3 4 4 6
  reroll 5 -> 2 3 4 4 5
  score small_straight = 30
turn 5: roll 1 1 4 4 4
  reroll 1 2 -> 6 4 4 4 4
  score four_of_a_kind = 22
turn 6: roll 6 6 6 1 2
  reroll 4 5 -> 6 6 6 6 5
  score sixes = 24
turn 7: roll 5 5 5 2 2
  score fives = 15
turn 8: roll 4 4 1 4 6
  reroll 3 5 -> 4 4 4 4 2
  score fours = 16
turn 9: roll 2 2 6 1 3
  reroll 3 4 -> 2 2 2 2 3
  reroll 5 -> 2 2 2 2 5
  score twos = 8
turn 10: roll 1 1 1 3 5
  reroll 4 -> 1 1 1 1 5
  score ones = 4
turn 11: roll 5 4 6 3 3
  score chance = 21
turn 12: roll 2 4 6 2 5
  score three_of_a_kind = 0
turn 13: roll 1 2 3 4 2
  score yahtzee = 0
scorecard
  ones            4
  twos            8
  threes          12
  fours           16
  fives           15
  sixes           24
  upper subtotal  79
  upper bonus     35
  three_of_a_kind 0
  four_of_a_kind  22
  full_house      25
  small_straight  30
  large_straight  40
  yahtzee         0
  chance          21
  yahtzee bonus   0
  total           252
EOF
)
play_case 'game A: rerolls, an earned upper bonus, honest zeroes' 0 "$expected" \
  fixtures/rollsA.txt fixtures/scriptA.txt

printf '%s\n' 4 4 4 4 4 6 6 6 6 6 6 6 6 6 6 2 2 3 3 3 4 4 4 4 4 1 2 3 4 6 5 5 2 2 5 \
  1 1 1 2 6 2 2 2 6 6 3 3 1 1 6 1 2 3 4 5 2 3 4 5 3 2 2 6 6 1 > fixtures/rollsB.txt
printf 'score %s\n' yahtzee sixes full_house three_of_a_kind fours chance fives ones \
  twos threes large_straight small_straight four_of_a_kind > fixtures/scriptB.txt
expected=$(cat <<'EOF'
turn 1: roll 4 4 4 4 4
  score yahtzee = 50
turn 2: roll 6 6 6 6 6
  yahtzee bonus! +100
  score sixes = 30
turn 3: roll 6 6 6 6 6
  yahtzee bonus! +100
  score full_house = 25
turn 4: roll 2 2 3 3 3
  score three_of_a_kind = 13
turn 5: roll 4 4 4 4 4
  yahtzee bonus! +100
  score fours = 20
turn 6: roll 1 2 3 4 6
  score chance = 16
turn 7: roll 5 5 2 2 5
  score fives = 15
turn 8: roll 1 1 1 2 6
  score ones = 3
turn 9: roll 2 2 2 6 6
  score twos = 6
turn 10: roll 3 3 1 1 6
  score threes = 6
turn 11: roll 1 2 3 4 5
  score large_straight = 40
turn 12: roll 2 3 4 5 3
  score small_straight = 30
turn 13: roll 2 2 6 6 1
  score four_of_a_kind = 0
scorecard
  ones            3
  twos            6
  threes          6
  fours           20
  fives           15
  sixes           30
  upper subtotal  80
  upper bonus     35
  three_of_a_kind 13
  four_of_a_kind  0
  full_house      25
  small_straight  30
  large_straight  40
  yahtzee         50
  chance          16
  yahtzee bonus   300
  total           589
EOF
)
play_case 'game B: yahtzee bonuses and both joker branches' 0 "$expected" \
  fixtures/rollsB.txt fixtures/scriptB.txt

# ---- play subcommand: rule violations stop the game --------------------------

printf '%s\n' 1 2 3 4 5 3 3 3 3 3 2 2 2 2 2 > fixtures/rollsC.txt
printf '%s\n' 'score yahtzee' 'score threes' > fixtures/scriptC.txt
expected=$(cat <<'EOF'
turn 1: roll 1 2 3 4 5
  score yahtzee = 0
turn 2: roll 3 3 3 3 3
  score threes = 15
turn 3: roll 2 2 2 2 2
EOF
)
play_err 'a zeroed yahtzee box pays no bonus on a later yahtzee' \
  "$expected" 'error: script ended early' fixtures/rollsC.txt fixtures/scriptC.txt

printf '%s\n' 2 2 2 2 2 5 5 5 5 5 > fixtures/rolls_jup.txt
printf '%s\n' 'score yahtzee' 'score chance' > fixtures/script_jup.txt
expected=$(cat <<'EOF'
turn 1: roll 2 2 2 2 2
  score yahtzee = 50
turn 2: roll 5 5 5 5 5
EOF
)
play_err 'joker: open matching upper box is mandatory' \
  "$expected" 'error: joker rule: must score fives' fixtures/rolls_jup.txt fixtures/script_jup.txt

printf '%s\n' 3 3 3 3 3 4 4 4 4 4 3 3 3 3 3 > fixtures/rolls_jlow.txt
printf '%s\n' 'score threes' 'score yahtzee' 'score sixes' > fixtures/script_jlow.txt
expected=$(cat <<'EOF'
turn 1: roll 3 3 3 3 3
  score threes = 15
turn 2: roll 4 4 4 4 4
  score yahtzee = 50
turn 3: roll 3 3 3 3 3
EOF
)
play_err 'joker: with the matching upper box gone, lower boxes come first' \
  "$expected" 'error: joker rule: score a lower category' fixtures/rolls_jlow.txt fixtures/script_jlow.txt

printf '%s\n' 1 2 3 4 5 6 1 > fixtures/rolls_rr3.txt
printf '%s\n' 'reroll 1' 'reroll 1' 'reroll 1' > fixtures/script_rr3.txt
expected=$(cat <<'EOF'
turn 1: roll 1 2 3 4 5
  reroll 1 -> 6 2 3 4 5
  reroll 1 -> 1 2 3 4 5
EOF
)
play_err 'third reroll in a turn is refused' \
  "$expected" 'error: too many rerolls' fixtures/rolls_rr3.txt fixtures/script_rr3.txt

printf '%s\n' 1 2 3 4 5 > fixtures/rolls5.txt
one_turn='turn 1: roll 1 2 3 4 5'

printf '%s\n' 'reroll 6' > fixtures/script_badpos.txt
play_err 'reroll position out of range' \
  "$one_turn" 'error: bad reroll: reroll 6' fixtures/rolls5.txt fixtures/script_badpos.txt

printf '%s\n' 'reroll 2 2' > fixtures/script_duppos.txt
play_err 'duplicate reroll position' \
  "$one_turn" 'error: bad reroll: reroll 2 2' fixtures/rolls5.txt fixtures/script_duppos.txt

printf '%s\n' 'score flush' > fixtures/script_unkcat.txt
play_err 'unknown category in the script' \
  "$one_turn" 'error: unknown category: flush' fixtures/rolls5.txt fixtures/script_unkcat.txt

printf '%s\n' 'keep 1 2' > fixtures/script_badline.txt
play_err 'unknown script command' \
  "$one_turn" 'error: bad script line: keep 1 2' fixtures/rolls5.txt fixtures/script_badline.txt

printf '%s\n' 'reroll 1' > fixtures/script_dry.txt
play_err 'rolls file runs dry on a reroll' \
  "$one_turn" 'error: out of rolls' fixtures/rolls5.txt fixtures/script_dry.txt

printf '%s\n' 1 2 3 4 5 6 6 1 2 3 > fixtures/rolls_used.txt
printf '%s\n' 'score chance' 'score chance' > fixtures/script_used.txt
expected=$(cat <<'EOF'
turn 1: roll 1 2 3 4 5
  score chance = 15
turn 2: roll 6 6 1 2 3
EOF
)
play_err 'category cannot be scored twice' \
  "$expected" 'error: category already used: chance' fixtures/rolls_used.txt fixtures/script_used.txt

printf '%s\n' 1 2 7 4 5 > fixtures/rolls_bad.txt
printf '%s\n' 'score chance' > fixtures/script_ok.txt
play_err 'rolls file is validated before play starts' \
  '' 'error: bad roll line: 7' fixtures/rolls_bad.txt fixtures/script_ok.txt

err_case 'play: unreadable rolls file' 'error: cannot read fixtures/norolls.txt' \
  play fixtures/norolls.txt fixtures/script_ok.txt
err_case 'play: unreadable script file' 'error: cannot read fixtures/noscript.txt' \
  play fixtures/rolls5.txt fixtures/noscript.txt

# ---- summary -----------------------------------------------------------------

printf '%d checks, %d failures\n' "$checks" "$fails"
if (( fails > 0 )); then
  exit 1
fi
printf 'OK\n'
