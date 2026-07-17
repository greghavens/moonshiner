#!/usr/bin/env bash
# Acceptance harness for dureport.sh.
# Run from the workspace root:  bash test_dureport.sh
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

run_stdin() { # run_stdin <dir> <infile> <cmd...> -- like run_in with stdin wired up
  local d=$1 f=$2
  shift 2
  ( cd "$d" && exec "$@" ) < "$f" > "$ROOT/$T/out" 2> "$ROOT/$T/err"
  RC=$?
  slurp OUT "$ROOT/$T/out"
  slurp ERR "$ROOT/$T/err"
}

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

if [[ ! -f dureport.sh ]]; then
  printf 'FAIL dureport.sh not found in the workspace root\n'
  exit 1
fi

# ---- fixtures: captured `du -k` dumps (children first, root last) -------------

printf '%s\t%s\n' \
  512 './assets/fonts' \
  204800 './assets/video/raw takes' \
  307200 './assets/video/proxies' \
  1048575 './assets/video' \
  900 './assets/audio/stems' \
  1500 './assets/audio' \
  16 './assets/tmp' \
  1051591 './assets' > "$T/media.du"

printf '%s\t%s\n' \
  100 './pool/delta' \
  100 './pool/alpha' \
  250 './pool/mid' \
  100 './pool/zeta' \
  600 './pool' > "$T/pool.du"

printf '%s\t%s\n' \
  1023 './b/k edge' \
  1024 './b/m edge' \
  1280 './b/half up' \
  1048576 './b/g edge' \
  2000000 './b' > "$T/edge.du"

printf '%s\t%s\n' 2048 './solo' > "$T/solo.du"

: > "$T/empty.du"

printf '%s\t%s\n' 10 './x/a' > "$T/bad1.du"
printf '%s\n' 'garbage line' >> "$T/bad1.du"
printf '%s\t%s\n' 90 './x' >> "$T/bad1.du"

printf '%s\t%s\n' '12x' './y/a' 44 './y' > "$T/bad2.du"

printf '5\t\n' > "$T/bad3.du"

# ---- full dump, defaults (--top 5 --depth 2) ----------------------------------

printf -v exp_default 'TOTAL\t1.0G\nDIRS (depth<=2)\n./assets/audio\t1.5M\n./assets/audio/stems\t900K\n./assets/fonts\t512K\n./assets/tmp\t16K\n./assets/video\t1024.0M\n./assets/video/proxies\t300.0M\n./assets/video/raw takes\t200.0M\nTOP 5\n./assets/video\t1024.0M\n./assets/video/proxies\t300.0M\n./assets/video/raw takes\t200.0M\n./assets/audio\t1.5M\n./assets/audio/stems\t900K\n'

run_in "$T" bash "$ROOT/dureport.sh" media.du
expect "media dump, defaults" 0 "$exp_default" ""
first_out=$OUT

run_in "$T" bash "$ROOT/dureport.sh" media.du
assert_eq "report is byte-stable across runs" "$first_out" "$OUT"

# ---- --depth 1 filters the DIRS table, not the TOP table ----------------------

printf -v exp_depth1 'TOTAL\t1.0G\nDIRS (depth<=1)\n./assets/audio\t1.5M\n./assets/fonts\t512K\n./assets/tmp\t16K\n./assets/video\t1024.0M\nTOP 5\n./assets/video\t1024.0M\n./assets/video/proxies\t300.0M\n./assets/video/raw takes\t200.0M\n./assets/audio\t1.5M\n./assets/audio/stems\t900K\n'

run_in "$T" bash "$ROOT/dureport.sh" --depth 1 media.du
expect "media dump, --depth 1" 0 "$exp_depth1" ""

# ---- --top 2 truncates the TOP table -------------------------------------------

printf -v exp_top2 'TOTAL\t1.0G\nDIRS (depth<=2)\n./assets/audio\t1.5M\n./assets/audio/stems\t900K\n./assets/fonts\t512K\n./assets/tmp\t16K\n./assets/video\t1024.0M\n./assets/video/proxies\t300.0M\n./assets/video/raw takes\t200.0M\nTOP 2\n./assets/video\t1024.0M\n./assets/video/proxies\t300.0M\n'

run_in "$T" bash "$ROOT/dureport.sh" --top 2 media.du
expect "media dump, --top 2" 0 "$exp_top2" ""

# flags may follow the filename
run_in "$T" bash "$ROOT/dureport.sh" media.du --top 2
expect "flag placed after the file" 0 "$exp_top2" ""

# ---- stdin via '-' --------------------------------------------------------------

run_stdin "$T" "$T/media.du" bash "$ROOT/dureport.sh" --top 2 -
expect "dump on stdin via -" 0 "$exp_top2" ""

# ---- ties: exact-KiB ordering, path tie-break, cutoff straddling ----------------

printf -v exp_pool3 'TOTAL\t600K\nDIRS (depth<=2)\n./pool/alpha\t100K\n./pool/delta\t100K\n./pool/mid\t250K\n./pool/zeta\t100K\nTOP 3\n./pool/mid\t250K\n./pool/alpha\t100K\n./pool/delta\t100K\n'

run_in "$T" bash "$ROOT/dureport.sh" --top 3 pool.du
expect "tie group straddling the --top cutoff" 0 "$exp_pool3" ""

printf -v exp_pool10 'TOTAL\t600K\nDIRS (depth<=2)\n./pool/alpha\t100K\n./pool/delta\t100K\n./pool/mid\t250K\n./pool/zeta\t100K\nTOP 10\n./pool/mid\t250K\n./pool/alpha\t100K\n./pool/delta\t100K\n./pool/zeta\t100K\n'

run_in "$T" bash "$ROOT/dureport.sh" --top 10 pool.du
expect "--top larger than the directory count" 0 "$exp_pool10" ""

# ---- unit thresholds and half-up rounding ---------------------------------------

printf -v exp_edge 'TOTAL\t1.9G\nDIRS (depth<=2)\n./b/g edge\t1.0G\n./b/half up\t1.3M\n./b/k edge\t1023K\n./b/m edge\t1.0M\nTOP 5\n./b/g edge\t1.0G\n./b/half up\t1.3M\n./b/m edge\t1.0M\n./b/k edge\t1023K\n'

run_in "$T" bash "$ROOT/dureport.sh" edge.du
expect "K/M/G thresholds and half-up rounding" 0 "$exp_edge" ""

# boundary: 1048575 KiB stays in the M band and prints 1024.0M (media fixture
# already pins this via ./assets/video)

# ---- one-line dump: headers still print ------------------------------------------

printf -v exp_solo 'TOTAL\t2.0M\nDIRS (depth<=2)\nTOP 5\n'

run_in "$T" bash "$ROOT/dureport.sh" solo.du
expect "root-only dump keeps both headers" 0 "$exp_solo" ""

# ---- invocation errors ------------------------------------------------------------

printf -v exp_usage 'usage: dureport.sh [--top N] [--depth D] <dufile|->\n'

run_in "$T" bash "$ROOT/dureport.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/dureport.sh" media.du pool.du
expect "two dump files" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/dureport.sh" --top 0 media.du
expect "--top 0 rejected" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/dureport.sh" --top nope media.du
expect "--top non-numeric" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/dureport.sh" --depth -2 media.du
expect "--depth negative" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/dureport.sh" --largest 3 media.du
expect "unknown flag" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/dureport.sh" media.du --top
expect "flag missing its value" 64 "" "$exp_usage"

# ---- unreadable / degenerate dumps -------------------------------------------------

printf -v exp_noread 'dureport.sh: cannot read: nope.du\n'
run_in "$T" bash "$ROOT/dureport.sh" nope.du
expect "missing dump file" 66 "" "$exp_noread"

printf -v exp_empty 'dureport.sh: empty du dump\n'
run_in "$T" bash "$ROOT/dureport.sh" empty.du
expect "empty dump" 65 "" "$exp_empty"

printf -v exp_bad2line 'dureport.sh: bad du record at line 2\n'
run_in "$T" bash "$ROOT/dureport.sh" bad1.du
expect "record without a tab" 65 "" "$exp_bad2line"

printf -v exp_bad1line 'dureport.sh: bad du record at line 1\n'
run_in "$T" bash "$ROOT/dureport.sh" bad2.du
expect "record with non-numeric size" 65 "" "$exp_bad1line"

run_in "$T" bash "$ROOT/dureport.sh" bad3.du
expect "record with empty path" 65 "" "$exp_bad1line"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf 'SUMMARY: %d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'SUMMARY: all %d checks passed\n' "$checks"
