#!/usr/bin/env bash
# Acceptance harness for snakes.sh (Snakes & Ladders round runner).
# Run from the workspace root:  bash test_snakes.sh
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- "${0%/*}"

if [[ ! -f ./snakes.sh ]]; then
  printf 'FAIL snakes.sh not found in the workspace root\n'
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

run() { "$BASH" ./snakes.sh "$@" </dev/null; }

rm -rf fixtures
mkdir fixtures

cat > fixtures/board20.txt <<'EOF'
# tiny tournament board
size 20

ladder 3 15
snake 17 5
ladder 8 12
snake 19 2
EOF

cat > fixtures/board12.txt <<'EOF'
size 12
ladder 2 9
snake 9 4
EOF

cat > fixtures/board10.txt <<'EOF'
size 10
snake 9 3
EOF

dice() { # dice <file> <roll>...
  local f=fixtures/$1
  shift
  printf '%s\n' "$@" > "$f"
}

game_case() { # game_case <label> <expected-status> <expected> <board> <dice> <players...>
  local label=$1 want_st=$2 expected=$3
  shift 3
  local out st
  out=$(run "$@"); st=$?
  assert_eq "$label" "$expected" "$out"
  assert_eq "$label: exit status" "$want_st" "$st"
}

# ---- full games ---------------------------------------------------------------

dice d1.txt 3 6 4 2 6 6 5 4 6 2
expected=$(cat <<'EOF'
round 1
  Alice rolls 3: 0 -> 3
    ladder! up to 15
  Bob rolls 6: 0 -> 6
round 2
  Alice rolls 4: 15 -> 19
    snake! down to 2
  Bob rolls 2: 6 -> 8
    ladder! up to 12
round 3
  Alice rolls 6: 2 -> 8
    ladder! up to 12
  Bob rolls 6: 12 -> 18
round 4
  Alice rolls 5: 12 -> 17
    snake! down to 5
  Bob rolls 4: 18 -> 18 (bounce)
round 5
  Alice rolls 6: 5 -> 11
  Bob rolls 2: 18 -> 20
Bob wins in round 5
EOF
)
game_case 'two players over snakes, ladders and a bounce' 0 "$expected" \
  fixtures/board20.txt fixtures/d1.txt Alice Bob

dice d2.txt 2 1 3
expected=$(cat <<'EOF'
round 1
  P rolls 2: 0 -> 2
    ladder! up to 9
  Q rolls 1: 0 -> 1
round 2
  P rolls 3: 9 -> 12
P wins in round 2
EOF
)
game_case 'ladder onto a snake head does not chain' 0 "$expected" \
  fixtures/board12.txt fixtures/d2.txt P Q

dice d3.txt 5 4 6 6
expected=$(cat <<'EOF'
round 1
  X rolls 5: 0 -> 5
  Y rolls 4: 0 -> 4
round 2
  X rolls 6: 5 -> 9 (bounce)
    snake! down to 3
  Y rolls 6: 4 -> 10
Y wins in round 2
EOF
)
game_case 'a bounce can land on a snake head' 0 "$expected" \
  fixtures/board10.txt fixtures/d3.txt X Y

dice d4.txt 4 3 2 5
expected=$(cat <<'EOF'
round 1
  Ann rolls 4: 0 -> 4
  Ben rolls 3: 0 -> 3
    ladder! up to 15
  Cy rolls 2: 0 -> 2
round 2
  Ann rolls 5: 4 -> 9
out of dice in round 2
positions:
  Ann 9
  Ben 15
  Cy 2
EOF
)
game_case 'three players run the dice dry mid-round' 1 "$expected" \
  fixtures/board20.txt fixtures/d4.txt Ann Ben Cy

dice d5.txt 1 1
expected=$(cat <<'EOF'
round 1
  P rolls 1: 0 -> 1
  Q rolls 1: 0 -> 1
round 2
out of dice in round 2
positions:
  P 1
  Q 1
EOF
)
game_case 'dice dry up exactly on a round boundary' 1 "$expected" \
  fixtures/board12.txt fixtures/d5.txt P Q

: > fixtures/d6.txt
expected=$(cat <<'EOF'
round 1
out of dice in round 1
positions:
  P 0
  Q 0
EOF
)
game_case 'empty dice file ends round 1 before anyone moves' 1 "$expected" \
  fixtures/board12.txt fixtures/d6.txt P Q

# ---- errors -------------------------------------------------------------------

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

usage='usage: snakes.sh <board-file> <dice-file> <player> <player>...'
err_case 'no arguments' "$usage"
err_case 'missing dice and players' "$usage" fixtures/board20.txt
err_case 'only one player' "$usage" fixtures/board20.txt fixtures/d1.txt Solo

err_case 'unreadable board file' 'error: cannot read fixtures/nope.txt' \
  fixtures/nope.txt fixtures/d1.txt A B
err_case 'unreadable dice file' 'error: cannot read fixtures/nodice.txt' \
  fixtures/board20.txt fixtures/nodice.txt A B

bad_board() { # bad_board <label> <expected-stderr> <board-lines...>
  local label=$1 expected=$2
  shift 2
  printf '%s\n' "$@" > fixtures/bad.txt
  err_case "$label" "$expected" fixtures/bad.txt fixtures/d1.txt A B
}

bad_board 'snake that climbs' 'error: bad board line: snake 5 9' 'size 20' 'snake 5 9'
bad_board 'ladder that sinks' 'error: bad board line: ladder 9 5' 'size 20' 'ladder 9 5'
bad_board 'unknown keyword' 'error: bad board line: walrus 1 2' 'size 20' 'walrus 1 2'
bad_board 'endpoint on the final square' 'error: bad board line: ladder 15 20' 'size 20' 'ladder 15 20'
bad_board 'endpoint below square 2' 'error: bad board line: snake 17 0' 'size 20' 'snake 17 0'
bad_board 'square with two departures' 'error: bad board line: snake 8 3' \
  'size 20' 'ladder 8 12' 'snake 8 3'
bad_board 'second size line' 'error: bad board line: size 20' 'size 20' 'size 20'
bad_board 'entry before the size line' 'error: bad board line: snake 17 5' 'snake 17 5' 'size 20'
bad_board 'non-numeric field' 'error: bad board line: snake x 2' 'size 20' 'snake x 2'
bad_board 'missing field' 'error: bad board line: snake 5' 'size 20' 'snake 5'
bad_board 'no size at all' 'error: board size missing' '# just a comment'

printf '3\n7\n' > fixtures/baddice.txt
err_case 'die out of range' 'error: bad dice line: 7' \
  fixtures/board20.txt fixtures/baddice.txt A B
printf '2 3\n' > fixtures/baddice2.txt
err_case 'two rolls on one line' 'error: bad dice line: 2 3' \
  fixtures/board20.txt fixtures/baddice2.txt A B

# ---- summary -----------------------------------------------------------------

printf '%d checks, %d failures\n' "$checks" "$fails"
if (( fails > 0 )); then
  exit 1
fi
printf 'OK\n'
