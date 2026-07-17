#!/usr/bin/env zsh
# Acceptance harness for war.zsh (card game War, deterministic fixture decks).
# Run from the workspace root:  zsh test_warcard.zsh
#
# war.zsh is meant for our zsh-only boxes: every invocation below launches it
# with an EMPTY PATH, so anything that forks out to an external tool fails
# loudly. Pure zsh only.
emulate -L zsh
setopt no_unset

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- "${0%/*}"

if [[ ! -f ./war.zsh ]]; then
  print -r -- 'FAIL war.zsh not found in the workspace root'
  exit 1
fi

ZSH_BIN=${commands[zsh]}

typeset -i checks=0 fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  (( checks++ ))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  (( fails++ ))
  print -r -- "FAIL $1"
  print -r -- '--- expected ---'
  print -r -- "$2"
  print -r -- '--- actual ---'
  print -r -- "$3"
  print -r -- '---------------'
}

run() { PATH='' "$ZSH_BIN" ./war.zsh "$@" </dev/null }

rm -rf fixtures
mkdir fixtures

deck() { # deck <file> <card>...
  local f=fixtures/$1
  shift
  print -rl -- "$@" > "$f"
}

game_case() { # game_case <label> <expected> <deck-a> <deck-b>
  local label=$1 expected=$2
  local out st
  out=$(run "fixtures/$3" "fixtures/$4"); st=$?
  assert_eq "$label" "$expected" "$out"
  assert_eq "$label: exit status" 0 "$st"
}

# ---- hand-checked small decks --------------------------------------------------

deck a_quick.txt AS KD QH
deck b_quick.txt 2C 3D 4S
expected=$(cat <<'EOF'
round 1: A AS vs B 2C -> A takes 2 (A 4, B 2)
round 2: A KD vs B 3D -> A takes 2 (A 5, B 1)
round 3: A QH vs B 4S -> A takes 2 (A 6, B 0)
A wins in 3 rounds
EOF
)
game_case 'plain battles run B out of cards' "$expected" a_quick.txt b_quick.txt

deck a_double.txt 7H 2C 3C 4C 8D 5S
deck b_double.txt 7S 9C TC JC 8H 6S
expected=$(cat <<'EOF'
round 1: A 7H vs B 7S -> war
  war: A burns 3, B burns 3
  A 8D vs B 8H -> war
  war: A burns 0, B burns 0
  A 5S vs B 6S -> B takes 12 (A 0, B 12)
B wins in 1 round
EOF
)
game_case 'a war inside a war, with last-card flips burning nothing' "$expected" \
  a_double.txt b_double.txt

deck a_oneside.txt 9H 2C
deck b_oneside.txt 9C
expected=$(cat <<'EOF'
round 1: A 9H vs B 9C -> war
  war: B has no cards left
A wins in 1 round
EOF
)
game_case 'tying with your last card loses the war on the spot' "$expected" \
  a_oneside.txt b_oneside.txt

deck a_draw.txt 9H
deck b_draw.txt 9C
expected=$(cat <<'EOF'
round 1: A 9H vs B 9C -> war
  war: nobody has cards left
draw after 1 round
EOF
)
game_case 'both last cards tie: the game is a draw' "$expected" a_draw.txt b_draw.txt

deck a_mid.txt 5H 2C 2D 2H 9S 4C 4D
deck b_mid.txt 5S 3C 3D 3H 8C 2S 6C
expected=$(cat <<'EOF'
round 1: A 5H vs B 5S -> war
  war: A burns 3, B burns 3
  A 9S vs B 8C -> A takes 10 (A 12, B 2)
round 2: A 4C vs B 2S -> A takes 2 (A 13, B 1)
round 3: A 4D vs B 6C -> B takes 2 (A 12, B 2)
round 4: A 5H vs B 4D -> A takes 2 (A 13, B 1)
round 5: A 2C vs B 6C -> B takes 2 (A 12, B 2)
round 6: A 2D vs B 2C -> war
  war: A burns 3, B burns 0
  A 3C vs B 6C -> B takes 7 (A 7, B 7)
round 7: A 3D vs B 2D -> A takes 2 (A 8, B 6)
round 8: A 3H vs B 2H -> A takes 2 (A 9, B 5)
round 9: A 8C vs B 9S -> B takes 2 (A 8, B 6)
round 10: A 4C vs B 5S -> B takes 2 (A 7, B 7)
round 11: A 2S vs B 3C -> B takes 2 (A 6, B 8)
round 12: A 5H vs B 2C -> A takes 2 (A 7, B 7)
round 13: A 4D vs B 6C -> B takes 2 (A 6, B 8)
round 14: A 3D vs B 8C -> B takes 2 (A 5, B 9)
round 15: A 2D vs B 9S -> B takes 2 (A 4, B 10)
round 16: A 3H vs B 4C -> B takes 2 (A 3, B 11)
round 17: A 2H vs B 5S -> B takes 2 (A 2, B 12)
round 18: A 5H vs B 2S -> A takes 2 (A 3, B 11)
round 19: A 2C vs B 3C -> B takes 2 (A 2, B 12)
round 20: A 5H vs B 4D -> A takes 2 (A 3, B 11)
round 21: A 2S vs B 6C -> B takes 2 (A 2, B 12)
round 22: A 5H vs B 3D -> A takes 2 (A 3, B 11)
round 23: A 4D vs B 8C -> B takes 2 (A 2, B 12)
round 24: A 5H vs B 2D -> A takes 2 (A 3, B 11)
round 25: A 3D vs B 9S -> B takes 2 (A 2, B 12)
round 26: A 5H vs B 3H -> A takes 2 (A 3, B 11)
round 27: A 2D vs B 4C -> B takes 2 (A 2, B 12)
round 28: A 5H vs B 2H -> A takes 2 (A 3, B 11)
round 29: A 3H vs B 5S -> B takes 2 (A 2, B 12)
round 30: A 5H vs B 2C -> A takes 2 (A 3, B 11)
round 31: A 2H vs B 3C -> B takes 2 (A 2, B 12)
round 32: A 5H vs B 2S -> A takes 2 (A 3, B 11)
round 33: A 2C vs B 6C -> B takes 2 (A 2, B 12)
round 34: A 5H vs B 4D -> A takes 2 (A 3, B 11)
round 35: A 2S vs B 8C -> B takes 2 (A 2, B 12)
round 36: A 5H vs B 3D -> A takes 2 (A 3, B 11)
round 37: A 4D vs B 9S -> B takes 2 (A 2, B 12)
round 38: A 5H vs B 2D -> A takes 2 (A 3, B 11)
round 39: A 3D vs B 4C -> B takes 2 (A 2, B 12)
round 40: A 5H vs B 3H -> A takes 2 (A 3, B 11)
round 41: A 2D vs B 5S -> B takes 2 (A 2, B 12)
round 42: A 5H vs B 2H -> A takes 2 (A 3, B 11)
round 43: A 3H vs B 3C -> war
  war: A burns 1, B burns 3
  A 2H vs B 8C -> B takes 8 (A 0, B 14)
B wins in 43 rounds
EOF
)
game_case 'mid-size decks: pickup order feeds later rounds' "$expected" a_mid.txt b_mid.txt

# ---- a full 52-card game --------------------------------------------------------

deck a_full.txt QS JC KH JD 4C AH 8D KC JH 7C 5H AS 6S 4D QD 9S TS TD 3S 2S AC KS 8H 9D AD 2H
deck b_full.txt TH QC 9H 5C 7D 3D 6D 7S 2C 3H 5S 3C 8S 8C 2D KD 4S 6H 4H 7H 9C 5D 6C JS QH TC
expected=$(cat <<'EOF'
round 1: A QS vs B TH -> A takes 2 (A 27, B 25)
round 2: A JC vs B QC -> B takes 2 (A 26, B 26)
round 3: A KH vs B 9H -> A takes 2 (A 27, B 25)
round 4: A JD vs B 5C -> A takes 2 (A 28, B 24)
round 5: A 4C vs B 7D -> B takes 2 (A 27, B 25)
round 6: A AH vs B 3D -> A takes 2 (A 28, B 24)
round 7: A 8D vs B 6D -> A takes 2 (A 29, B 23)
round 8: A KC vs B 7S -> A takes 2 (A 30, B 22)
round 9: A JH vs B 2C -> A takes 2 (A 31, B 21)
round 10: A 7C vs B 3H -> A takes 2 (A 32, B 20)
round 11: A 5H vs B 5S -> war
  war: A burns 3, B burns 3
  A QD vs B 2D -> A takes 10 (A 37, B 15)
round 12: A 9S vs B KD -> B takes 2 (A 36, B 16)
round 13: A TS vs B 4S -> A takes 2 (A 37, B 15)
round 14: A TD vs B 6H -> A takes 2 (A 38, B 14)
round 15: A 3S vs B 4H -> B takes 2 (A 37, B 15)
round 16: A 2S vs B 7H -> B takes 2 (A 36, B 16)
round 17: A AC vs B 9C -> A takes 2 (A 37, B 15)
round 18: A KS vs B 5D -> A takes 2 (A 38, B 14)
round 19: A 8H vs B 6C -> A takes 2 (A 39, B 13)
round 20: A 9D vs B JS -> B takes 2 (A 38, B 14)
round 21: A AD vs B QH -> A takes 2 (A 39, B 13)
round 22: A 2H vs B TC -> B takes 2 (A 38, B 14)
round 23: A QS vs B JC -> A takes 2 (A 39, B 13)
round 24: A TH vs B QC -> B takes 2 (A 38, B 14)
round 25: A KH vs B 4C -> A takes 2 (A 39, B 13)
round 26: A 9H vs B 7D -> A takes 2 (A 40, B 12)
round 27: A JD vs B 9S -> A takes 2 (A 41, B 11)
round 28: A 5C vs B KD -> B takes 2 (A 40, B 12)
round 29: A AH vs B 3S -> A takes 2 (A 41, B 11)
round 30: A 3D vs B 4H -> B takes 2 (A 40, B 12)
round 31: A 8D vs B 2S -> A takes 2 (A 41, B 11)
round 32: A 6D vs B 7H -> B takes 2 (A 40, B 12)
round 33: A KC vs B 9D -> A takes 2 (A 41, B 11)
round 34: A 7S vs B JS -> B takes 2 (A 40, B 12)
round 35: A JH vs B 2H -> A takes 2 (A 41, B 11)
round 36: A 2C vs B TC -> B takes 2 (A 40, B 12)
round 37: A 7C vs B TH -> B takes 2 (A 39, B 13)
round 38: A 3H vs B QC -> B takes 2 (A 38, B 14)
round 39: A 5H vs B 5C -> war
  war: A burns 3, B burns 3
  A QD vs B 6D -> A takes 10 (A 43, B 9)
round 40: A 5S vs B 7H -> B takes 2 (A 42, B 10)
round 41: A 3C vs B 7S -> B takes 2 (A 41, B 11)
round 42: A 8S vs B JS -> B takes 2 (A 40, B 12)
round 43: A 8C vs B 2C -> A takes 2 (A 41, B 11)
round 44: A 2D vs B TC -> B takes 2 (A 40, B 12)
round 45: A TS vs B 7C -> A takes 2 (A 41, B 11)
round 46: A 4S vs B TH -> B takes 2 (A 40, B 12)
round 47: A TD vs B 3H -> A takes 2 (A 41, B 11)
round 48: A 6H vs B QC -> B takes 2 (A 40, B 12)
round 49: A AC vs B 5S -> A takes 2 (A 41, B 11)
round 50: A 9C vs B 7H -> A takes 2 (A 42, B 10)
round 51: A KS vs B 3C -> A takes 2 (A 43, B 9)
round 52: A 5D vs B 7S -> B takes 2 (A 42, B 10)
round 53: A 8H vs B 8S -> war
  war: A burns 3, B burns 3
  A QS vs B 4S -> A takes 10 (A 47, B 5)
round 54: A JC vs B TH -> A takes 2 (A 48, B 4)
round 55: A KH vs B 6H -> A takes 2 (A 49, B 3)
round 56: A 4C vs B QC -> B takes 2 (A 48, B 4)
round 57: A 9H vs B 5D -> A takes 2 (A 49, B 3)
round 58: A 7D vs B 7S -> war
  war: A burns 3, B burns 1
  A 3S vs B QC -> B takes 8 (A 44, B 8)
round 59: A 8D vs B 7D -> A takes 2 (A 45, B 7)
round 60: A 2S vs B JD -> B takes 2 (A 44, B 8)
round 61: A KC vs B 9S -> A takes 2 (A 45, B 7)
round 62: A 9D vs B AH -> B takes 2 (A 44, B 8)
round 63: A JH vs B 3S -> A takes 2 (A 45, B 7)
round 64: A 2H vs B 7S -> B takes 2 (A 44, B 8)
round 65: A 5H vs B 4C -> A takes 2 (A 45, B 7)
round 66: A AS vs B QC -> A takes 2 (A 46, B 6)
round 67: A 6S vs B 2S -> A takes 2 (A 47, B 5)
round 68: A 4D vs B JD -> B takes 2 (A 46, B 6)
round 69: A QD vs B 9D -> A takes 2 (A 47, B 5)
round 70: A 5C vs B AH -> B takes 2 (A 46, B 6)
round 71: A KD vs B 2H -> A takes 2 (A 47, B 5)
round 72: A 3D vs B 7S -> B takes 2 (A 46, B 6)
round 73: A 4H vs B 4D -> war
  war: A burns 3, B burns 3
  A TS vs B 3D -> A takes 10 (A 51, B 1)
round 74: A 7C vs B 7S -> war
  war: B has no cards left
A wins in 74 rounds
EOF
)
game_case 'full 52-card deal plays out to the end' "$expected" a_full.txt b_full.txt

# ---- errors ---------------------------------------------------------------------

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

usage='usage: war.zsh <deck-a-file> <deck-b-file>'
err_case 'no arguments' "$usage"
err_case 'one argument' "$usage" fixtures/a_quick.txt
err_case 'three arguments' "$usage" fixtures/a_quick.txt fixtures/b_quick.txt extra

err_case 'unreadable deck A' 'error: cannot read fixtures/nope.txt' \
  fixtures/nope.txt fixtures/b_quick.txt
err_case 'unreadable deck B' 'error: cannot read fixtures/nope.txt' \
  fixtures/a_quick.txt fixtures/nope.txt

deck a_bad1.txt AS 1H 2C
err_case 'rank 1 is not a card' 'error: invalid card: 1H' fixtures/a_bad1.txt fixtures/b_quick.txt
deck b_badsuit.txt KX
err_case 'suit X is not a card' 'error: invalid card: KX' fixtures/a_quick.txt fixtures/b_badsuit.txt
deck a_bad10.txt 10D
err_case 'tens are written T' 'error: invalid card: 10D' fixtures/a_bad10.txt fixtures/b_quick.txt

: > fixtures/a_empty.txt
err_case 'empty deck file' 'error: empty deck: fixtures/a_empty.txt' \
  fixtures/a_empty.txt fixtures/b_quick.txt

# ---- summary --------------------------------------------------------------------

print -r -- "$checks checks, $fails failures"
if (( fails > 0 )); then
  exit 1
fi
print -r -- OK
