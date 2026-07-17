#!/usr/bin/env bash
# Acceptance harness for renum.sh.
# Run from the workspace root:  bash test_renum.sh
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

assert_content() { # assert_content <label> <file> <expected-one-line-content>
  checks=$((checks + 1))
  local got=''
  if [[ -f "$2" ]]; then
    IFS= read -r got < "$2" || true
    if [[ "$got" == "$3" ]]; then
      printf 'PASS %s\n' "$1"
      return 0
    fi
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (file %s: expected %q, got %q)\n' "$1" "$2" "$3" "${got:-<missing>}"
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

listing() { # listing <dir> -- C-sorted one-per-line dir listing incl. dotfiles
  ( cd "$1" && ls -A )
}

if [[ ! -f renum.sh ]]; then
  printf 'FAIL renum.sh not found in the workspace root\n'
  exit 1
fi

# ---- fixture A: mixed scan-station batch ---------------------------------------

A="$T/batchA"
mkdir -p "$A/raw"
printf 'photo two\n'   > "$A/IMG 0007.JPG"
printf 'photo one\n'   > "$A/IMG 0002.JPG"
printf 'video clip\n'  > "$A/clip.mov"
printf 'plain notes\n' > "$A/notes"
printf 'cache junk\n'  > "$A/.thumbcache"

printf -v exp_dryA 'IMG 0002.JPG -> trip-01.JPG\nIMG 0007.JPG -> trip-02.JPG\nclip.mov -> trip-03.mov\nnotes -> trip-04\nplan: 4 rename(s), 0 unchanged\n'

run_in "$T" bash "$ROOT/renum.sh" --dry-run batchA trip-
expect "dry-run plan for a mixed batch" 0 "$exp_dryA" ""

printf -v exp_lsA_before '.thumbcache\nIMG 0002.JPG\nIMG 0007.JPG\nclip.mov\nnotes\nraw'
assert_eq "dry-run changes nothing" "$exp_lsA_before" "$(listing "$A")"

run_in "$T" bash "$ROOT/renum.sh" batchA trip-
printf -v exp_runA 'renamed: 4 file(s), 0 unchanged\n'
expect "real run renames the batch" 0 "$exp_runA" ""

printf -v exp_lsA_after '.thumbcache\nraw\ntrip-01.JPG\ntrip-02.JPG\ntrip-03.mov\ntrip-04'
assert_eq "directory after rename (dotfile and subdir untouched)" "$exp_lsA_after" "$(listing "$A")"
assert_content "trip-01.JPG holds IMG 0002.JPG's bytes" "$A/trip-01.JPG" 'photo one'
assert_content "trip-02.JPG holds IMG 0007.JPG's bytes" "$A/trip-02.JPG" 'photo two'
assert_content "trip-03.mov holds clip.mov's bytes" "$A/trip-03.mov" 'video clip'
assert_content "trip-04 holds notes' bytes" "$A/trip-04" 'plain notes'
assert_content "dotfile left alone" "$A/.thumbcache" 'cache junk'

# re-run: pure no-op
run_in "$T" bash "$ROOT/renum.sh" batchA trip-
printf -v exp_rerunA 'renamed: 0 file(s), 4 unchanged\n'
expect "re-run on a numbered directory is a no-op" 0 "$exp_rerunA" ""

# late arrival: only the newcomer moves
printf 'late scan\n' > "$A/zzz late.mov"
run_in "$T" bash "$ROOT/renum.sh" batchA trip-
printf -v exp_lateA 'renamed: 1 file(s), 4 unchanged\n'
expect "late arrival picks up the next number" 0 "$exp_lateA" ""
assert_content "late arrival becomes trip-05.mov" "$A/trip-05.mov" 'late scan'

# ---- fixture B: overlapping old/new names must not eat bytes --------------------

B="$T/batchB"
mkdir -p "$B"
printf 'alpha\n'   > "$B/shot-01.jpg"
printf 'bravo\n'   > "$B/shot-02.jpg"
printf 'charlie\n' > "$B/shot-03.jpg"

printf -v exp_dryB 'shot-01.jpg -> shot-02.jpg\nshot-02.jpg -> shot-03.jpg\nshot-03.jpg -> shot-04.jpg\nplan: 3 rename(s), 0 unchanged\n'
run_in "$T" bash "$ROOT/renum.sh" --dry-run --start 2 batchB shot-
expect "dry-run plan for the overlapping shift" 0 "$exp_dryB" ""

run_in "$T" bash "$ROOT/renum.sh" --start 2 batchB shot-
printf -v exp_runB 'renamed: 3 file(s), 0 unchanged\n'
expect "overlapping shift succeeds" 0 "$exp_runB" ""

printf -v exp_lsB 'shot-02.jpg\nshot-03.jpg\nshot-04.jpg'
assert_eq "overlapping shift leaves exactly the three finals" "$exp_lsB" "$(listing "$B")"
assert_content "shot-02.jpg holds old shot-01.jpg's bytes" "$B/shot-02.jpg" 'alpha'
assert_content "shot-03.jpg holds old shot-02.jpg's bytes" "$B/shot-03.jpg" 'bravo'
assert_content "shot-04.jpg holds old shot-03.jpg's bytes" "$B/shot-04.jpg" 'charlie'

# ---- fixture C: target occupied by a non-batch entry -> refuse untouched ---------

C="$T/batchC"
mkdir -p "$C/shot-04.jpg"
printf 'alpha\n'   > "$C/shot-01.jpg"
printf 'bravo\n'   > "$C/shot-02.jpg"
printf 'charlie\n' > "$C/shot-03.jpg"

printf -v exp_conflict 'renum.sh: target exists outside the batch: shot-04.jpg\n'
run_in "$T" bash "$ROOT/renum.sh" --start 2 batchC shot-
expect "conflicting non-batch target refused" 65 "" "$exp_conflict"

printf -v exp_lsC 'shot-01.jpg\nshot-02.jpg\nshot-03.jpg\nshot-04.jpg'
assert_eq "refused run touches nothing" "$exp_lsC" "$(listing "$C")"
assert_content "shot-01.jpg untouched after refusal" "$C/shot-01.jpg" 'alpha'
assert_content "shot-02.jpg untouched after refusal" "$C/shot-02.jpg" 'bravo'
assert_content "shot-03.jpg untouched after refusal" "$C/shot-03.jpg" 'charlie'

# ---- fixture D: width defaults and overrides -------------------------------------

D="$T/batchD"
mkdir -p "$D"
printf 'a\n' > "$D/a.png"
printf 'b\n' > "$D/b.png"
printf 'c\n' > "$D/c.png"

run_in "$T" bash "$ROOT/renum.sh" --dry-run --start 998 batchD f-
printf -v exp_dryD 'a.png -> f-0998.png\nb.png -> f-0999.png\nc.png -> f-1000.png\nplan: 3 rename(s), 0 unchanged\n'
expect "default width fits the largest number" 0 "$exp_dryD" ""

run_in "$T" bash "$ROOT/renum.sh" --dry-run --start 998 --width 6 batchD f-
printf -v exp_dryD6 'a.png -> f-000998.png\nb.png -> f-000999.png\nc.png -> f-001000.png\nplan: 3 rename(s), 0 unchanged\n'
expect "--width override pads wider" 0 "$exp_dryD6" ""

printf -v exp_narrow 'renum.sh: --width too small: need 4 digit(s)\n'
run_in "$T" bash "$ROOT/renum.sh" --start 998 --width 3 batchD f-
expect "--width too small refused" 65 "" "$exp_narrow"

printf -v exp_lsD 'a.png\nb.png\nc.png'
assert_eq "refused width run touches nothing" "$exp_lsD" "$(listing "$D")"

# ---- invocation and environment errors -------------------------------------------

printf -v exp_usage 'usage: renum.sh [--dry-run] [--start N] [--width W] <dir> <prefix>\n'

run_in "$T" bash "$ROOT/renum.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/renum.sh" batchD
expect "missing prefix" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/renum.sh" batchD f- extra
expect "extra positional" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/renum.sh" batchD ''
expect "empty prefix" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/renum.sh" --start 0 batchD f-
expect "--start 0 rejected" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/renum.sh" --start x batchD f-
expect "--start non-numeric" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/renum.sh" --width 0 batchD f-
expect "--width 0 rejected" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/renum.sh" --shuffle batchD f-
expect "unknown flag" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/renum.sh" batchD f- --start
expect "flag missing its value" 64 "" "$exp_usage"

printf -v exp_nodir 'renum.sh: not a directory: gone\n'
run_in "$T" bash "$ROOT/renum.sh" gone f-
expect "missing directory" 66 "" "$exp_nodir"

E="$T/batchE"
mkdir -p "$E/onlydir"
printf -v exp_nofiles 'renum.sh: no regular files in: batchE\n'
run_in "$T" bash "$ROOT/renum.sh" batchE f-
expect "directory without regular files" 65 "" "$exp_nofiles"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf 'SUMMARY: %d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'SUMMARY: all %d checks passed\n' "$checks"
