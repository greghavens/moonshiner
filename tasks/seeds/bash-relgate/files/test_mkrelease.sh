#!/usr/bin/env bash
# Regression harness for mkrelease.sh.
# Run from the workspace root:  bash test_mkrelease.sh
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

assert_true() { # assert_true <label> <rc-of-condition>
  checks=$((checks + 1))
  if [[ "$2" -eq 0 ]]; then
    printf 'PASS %s\n' "$1"
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n' "$1"
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

if [[ ! -f mkrelease.sh ]]; then
  printf 'FAIL mkrelease.sh not found in the workspace root\n'
  exit 1
fi

# ---- build-tree fixtures -----------------------------------------------------------

mk_tree() { # mk_tree <dir>  -- healthy three-artifact build tree
  mkdir -p "$1/assets" "$1/notes"
  printf '7.3.1\n' > "$1/VERSION"
  printf 'app.bin\t4\nassets/logo.png\t2\nnotes/README\t1\n' > "$1/MANIFEST"
  printf 'binary payload v7.3.1\n'  > "$1/app.bin"
  printf 'PNGish logo bytes\n'      > "$1/assets/logo.png"
  printf 'release notes stub\n'     > "$1/notes/README"
}

mk_tree "$T/build"

# missing artifact: MANIFEST names assets/logo.png but the file is absent
mk_tree "$T/build-missing"
rm "$T/build-missing/assets/logo.png"

# over budget: media/big.bin is 2048 bytes against a 1-KB cap
mk_tree "$T/build-budget"
mkdir -p "$T/build-budget/media"
awk 'BEGIN { for (i = 0; i < 128; i++) printf "0123456789abcdef" }' > "$T/build-budget/media/big.bin"
printf 'app.bin\t4\nassets/logo.png\t2\nmedia/big.bin\t1\nnotes/README\t1\n' > "$T/build-budget/MANIFEST"

# half-synced trees
mk_tree "$T/build-noversion"
rm "$T/build-noversion/VERSION"
mk_tree "$T/build-nomanifest"
rm "$T/build-nomanifest/MANIFEST"

# ---- happy path ---------------------------------------------------------------------

printf -v exp_ok 'mkrelease: version 7.3.1\nmkrelease: staged app.bin\nmkrelease: staged assets/logo.png\nmkrelease: staged notes/README\nmkrelease: stage populated\nmkrelease: artifacts in place\nmkrelease: receipt written\nmkrelease: sealed\nmkrelease: release OK: rel-7.3.1\n'

run_in "$T" bash "$ROOT/mkrelease.sh" build out-ok
expect "healthy tree releases" 0 "$exp_ok" ""

REL="$T/out-ok/rel-7.3.1"
for f in app.bin assets/logo.png notes/README; do
  checks=$((checks + 1))
  if cmp -s "$T/build/$f" "$REL/$f"; then
    printf 'PASS staged %s matches the build tree\n' "$f"
  else
    fails=$((fails + 1))
    printf 'FAIL staged %s matches the build tree\n' "$f"
  fi
done

exp_receipt=''
for f in app.bin assets/logo.png notes/README; do
  set -- $(cksum "$T/build/$f")
  exp_receipt+="$f	$1	$2"$'\n'
done
RECEIPT=''
if [[ -f "$REL/RECEIPT" ]]; then
  slurp RECEIPT "$REL/RECEIPT"
fi
assert_eq "RECEIPT lists path, crc, bytes per artifact" "$exp_receipt" "$RECEIPT"

# re-run over the same outdir: byte-identical result
run_in "$T" bash "$ROOT/mkrelease.sh" build out-ok
expect "re-release over an existing outdir" 0 "$exp_ok" ""
RECEIPT=''
slurp RECEIPT "$REL/RECEIPT"
assert_eq "RECEIPT stable across re-runs" "$exp_receipt" "$RECEIPT"

# ---- a missing artifact must abort staging, name the file, leave nothing ------------

printf -v exp_missing_out 'mkrelease: version 7.3.1\nmkrelease: staged app.bin\n'
printf -v exp_missing_err 'mkrelease: staging failed: assets/logo.png\n'

run_in "$T" bash "$ROOT/mkrelease.sh" build-missing out-missing
expect "missing artifact aborts staging" 70 "$exp_missing_out" "$exp_missing_err"
assert_eq "no release dir survives a staging failure" "" "$(ls -A "$T/out-missing" 2>/dev/null)"

# ---- an over-budget artifact must refuse to seal ------------------------------------

printf -v exp_budget_out 'mkrelease: version 7.3.1\nmkrelease: staged app.bin\nmkrelease: staged assets/logo.png\nmkrelease: staged media/big.bin\nmkrelease: staged notes/README\nmkrelease: stage populated\nmkrelease: artifacts in place\n'
printf -v exp_budget_err 'mkrelease: over budget: media/big.bin (2048 > 1024)\n'

run_in "$T" bash "$ROOT/mkrelease.sh" build-budget out-budget
expect "over-budget artifact refuses to seal" 71 "$exp_budget_out" "$exp_budget_err"
assert_eq "no release dir survives a budget failure" "" "$(ls -A "$T/out-budget" 2>/dev/null)"

# ---- half-synced trees ---------------------------------------------------------------

printf -v exp_nover_err 'mkrelease: missing VERSION in build-noversion\n'
run_in "$T" bash "$ROOT/mkrelease.sh" build-noversion out-nover
expect "tree without VERSION is refused" 65 "" "$exp_nover_err"
assert_eq "no rel- dir for a versionless tree" "" "$(ls -A "$T/out-nover" 2>/dev/null)"

printf -v exp_noman_out 'mkrelease: version 7.3.1\n'
printf -v exp_noman_err 'mkrelease: no MANIFEST in build-nomanifest\n'
run_in "$T" bash "$ROOT/mkrelease.sh" build-nomanifest out-noman
expect "tree without MANIFEST is refused" 67 "$exp_noman_out" "$exp_noman_err"
assert_eq "no rel- dir for a manifestless tree" "" "$(ls -A "$T/out-noman" 2>/dev/null)"

# ---- invocation errors ----------------------------------------------------------------

printf -v exp_usage 'usage: mkrelease.sh <builddir> <outdir>\n'

run_in "$T" bash "$ROOT/mkrelease.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/mkrelease.sh" build
expect "missing outdir" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/mkrelease.sh" build out extra
expect "extra argument" 64 "" "$exp_usage"

printf -v exp_nodir 'mkrelease: not a directory: nowhere\n'
run_in "$T" bash "$ROOT/mkrelease.sh" nowhere out
expect "builddir must exist" 66 "" "$exp_nodir"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf 'SUMMARY: %d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'SUMMARY: all %d checks passed\n' "$checks"
