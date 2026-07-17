#!/usr/bin/env zsh
# Acceptance harness for ttt.zsh (stdin-scripted tic-tac-toe).
# Run from the workspace root:  zsh test_ttt.zsh
#
# ttt.zsh is meant for our zsh-only boxes: every invocation below launches it
# with an EMPTY PATH, so anything that forks out to an external tool fails
# loudly. Pure zsh only.
emulate -L zsh
setopt no_unset

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- "${0%/*}"

if [[ ! -f ./ttt.zsh ]]; then
  print -r -- 'FAIL ttt.zsh not found in the workspace root'
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

run() { PATH='' "$ZSH_BIN" ./ttt.zsh "$@" }

rm -rf fixtures
mkdir fixtures

game_case() { # game_case <label> <expected-status> <expected> <move>...
  local label=$1 want_st=$2 expected=$3
  shift 3
  local out st
  out=$(print -rl -- "$@" | run); st=$?
  assert_eq "$label" "$expected" "$out"
  assert_eq "$label: exit status" "$want_st" "$st"
}

expected=$(cat <<'EOF'
X plays a1
  1 2 3
a X . .
b . . .
c . . .
O plays b1
  1 2 3
a X . .
b O . .
c . . .
X plays a2
  1 2 3
a X X .
b O . .
c . . .
O plays b2
  1 2 3
a X X .
b O O .
c . . .
X plays a3
  1 2 3
a X X X
b O O .
c . . .
X wins
EOF
)
game_case 'X takes the top row' 0 "$expected" a1 b1 a2 b2 a3

expected=$(cat <<'EOF'
X plays a1
  1 2 3
a X . .
b . . .
c . . .
O plays a3
  1 2 3
a X . O
b . . .
c . . .
X plays b2
  1 2 3
a X . O
b . X .
c . . .
O plays b3
  1 2 3
a X . O
b . X O
c . . .
X plays c1
  1 2 3
a X . O
b . X O
c X . .
O plays c3
  1 2 3
a X . O
b . X O
c X . O
O wins
EOF
)
game_case 'O takes the third column' 0 "$expected" a1 a3 b2 b3 c1 c3

expected=$(cat <<'EOF'
X plays b2
  1 2 3
a . . .
b . X .
c . . .
O plays a2
  1 2 3
a . O .
b . X .
c . . .
X plays a1
  1 2 3
a X O .
b . X .
c . . .
O plays a3
  1 2 3
a X O O
b . X .
c . . .
X plays c3
  1 2 3
a X O O
b . X .
c . . X
X wins
EOF
)
game_case 'X takes the main diagonal' 0 "$expected" b2 a2 a1 a3 c3

expected=$(cat <<'EOF'
X plays a3
  1 2 3
a . . X
b . . .
c . . .
O plays b1
  1 2 3
a . . X
b O . .
c . . .
X plays b2
  1 2 3
a . . X
b O X .
c . . .
O plays c2
  1 2 3
a . . X
b O X .
c . O .
X plays c1
  1 2 3
a . . X
b O X .
c X O .
X wins
EOF
)
game_case 'uppercase moves land on the anti-diagonal' 0 "$expected" A3 b1 B2 c2 C1

expected=$(cat <<'EOF'
X plays a1
  1 2 3
a X . .
b . . .
c . . .
O plays a2
  1 2 3
a X O .
b . . .
c . . .
X plays a3
  1 2 3
a X O X
b . . .
c . . .
O plays b2
  1 2 3
a X O X
b . O .
c . . .
X plays b1
  1 2 3
a X O X
b X O .
c . . .
O plays b3
  1 2 3
a X O X
b X O O
c . . .
X plays c2
  1 2 3
a X O X
b X O O
c . X .
O plays c1
  1 2 3
a X O X
b X O O
c O X .
X plays c3
  1 2 3
a X O X
b X O O
c O X X
draw
EOF
)
game_case 'a full board with no line is a draw' 0 "$expected" a1 a2 a3 b2 b1 b3 c2 c1 c3

expected=$(cat <<'EOF'
X plays a1
  1 2 3
a X . .
b . . .
c . . .
O plays b2
  1 2 3
a X . .
b . O .
c . . .
X plays a2
  1 2 3
a X X .
b . O .
c . . .
O plays b3
  1 2 3
a X X .
b . O O
c . . .
X plays b1
  1 2 3
a X X .
b X O O
c . . .
O plays c1
  1 2 3
a X X .
b X O O
c O . .
X plays c2
  1 2 3
a X X .
b X O O
c O X .
O plays c3
  1 2 3
a X X .
b X O O
c O X O
X plays a3
  1 2 3
a X X X
b X O O
c O X O
X wins
EOF
)
game_case 'a win on the ninth move beats the draw call' 0 "$expected" a1 b2 a2 b3 b1 c1 c2 c3 a3

expected=$(cat <<'EOF'
X plays a1
  1 2 3
a X . .
b . . .
c . . .
square a1 is taken
invalid move: d4
invalid move: a1x
O plays b2
  1 2 3
a X . .
b . O .
c . . .
game aborted
EOF
)
game_case 'taken squares and junk moves cost no turn' 1 "$expected" a1 A1 d4 '' a1x b2

out=$(run </dev/null); st=$?
assert_eq 'no input at all aborts the game' 'game aborted' "$out"
assert_eq 'no input at all: exit status' 1 "$st"

out=$(run extra 2>fixtures/.stderr </dev/null); st=$?
err=$(<fixtures/.stderr)
assert_eq 'arguments are refused: exit status' 2 "$st"
assert_eq 'arguments are refused: stderr' 'usage: ttt.zsh' "$err"
assert_eq 'arguments are refused: stdout stays empty' '' "$out"

# ---- summary --------------------------------------------------------------------

print -r -- "$checks checks, $fails failures"
if (( fails > 0 )); then
  exit 1
fi
print -r -- OK
