#!/usr/bin/env bash
# Acceptance harness for the runjobs.sh rework (string-built commands -> argv).
# Run from the workspace root:  bash test_filedrop.sh
#
# Two halves:
#   LEGACY BEHAVIOR -- everything runjobs.sh already does today, re-pinned.
#   REAL-WORLD NAMES -- the tickets that motivated the rework: filenames
#   containing quote characters, plus the no-string-execution gate.
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

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
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

slurp() { # slurp <var> <file> -- byte-exact contents; missing file reads as empty
  printf -v "$1" ''
  [[ -f "$2" ]] || return 0
  IFS= read -r -d '' "$1" < "$2" || true
}

RC=0
OUT=''
ERR=''
run_jobs() { # run_jobs <jobs> <src> <out>
  ( cd "$ROOT/$T" && exec bash "$ROOT/runjobs.sh" "$@" ) \
    > "$ROOT/$T/.out" 2> "$ROOT/$T/.err"
  RC=$?
  slurp OUT "$ROOT/$T/.out"
  slurp ERR "$ROOT/$T/.err"
}

if [[ ! -f runjobs.sh ]]; then
  printf 'FAIL runjobs.sh not found in the workspace root\n'
  exit 1
fi

nl=$'\n'
sq=\'

# =================== LEGACY BEHAVIOR (must keep passing) ======================

mkdir -p "$ROOT/$T/drop"
printf 'plain contents\n'            > "$ROOT/$T/drop/plain.txt"
printf 'q3 totals: 41,220.55\n'      > "$ROOT/$T/drop/Monthly Report \$Q3.txt"

# ---- 1. a clean day: copy, pack, note, multi-word flags ----------------------
{
  printf 'copy\tplain.txt\t\n'
  printf 'copy\tMonthly Report $Q3.txt\t-p -f\n'
  printf 'pack\tplain.txt\t-9\n'
  printf 'note\tMonthly Report $Q3.txt\t\n'
  printf 'note\tplain.txt\t\n'
} > "$ROOT/$T/day1.tsv"

run_jobs day1.tsv drop out1
assert_eq 'clean day: exit code' '0' "$RC"
assert_eq 'clean day: stderr is quiet' '' "$ERR"
assert_eq 'clean day: stdout' "jobs ok: 5${nl}jobs failed: 0${nl}" "$OUT"

checks=$((checks + 1))
if ! cmp -s "$ROOT/$T/drop/plain.txt" "$ROOT/$T/out1/plain.txt"; then
  fails=$((fails + 1)); printf 'FAIL clean day: plain.txt not copied intact\n'
fi
checks=$((checks + 1))
if ! cmp -s "$ROOT/$T/drop/Monthly Report \$Q3.txt" "$ROOT/$T/out1/Monthly Report \$Q3.txt"; then
  fails=$((fails + 1)); printf 'FAIL clean day: spaced/dollar name not copied intact\n'
fi

UNPACKED=$(gzip -dc "$ROOT/$T/out1/plain.txt.gz" 2>/dev/null || echo '(gunzip failed)')
assert_eq 'clean day: pack output decompresses to the source' \
  'plain contents' "$UNPACKED"

LOG=''
slurp LOG "$ROOT/$T/out1/filing.log"
assert_eq 'clean day: filing.log lines in job order' \
  "filed: Monthly Report \$Q3.txt${nl}filed: plain.txt${nl}" "$LOG"

# ---- 2. failures are counted, reported, and non-zero -------------------------
{
  printf 'copy\tplain.txt\t\n'
  printf 'copy\tno-such-file.txt\t\n'
  printf 'shred\tplain.txt\t\n'
} > "$ROOT/$T/day2.tsv"

run_jobs day2.tsv drop out2
assert_eq 'failures: exit code' '1' "$RC"
assert_eq 'failures: stdout' "jobs ok: 1${nl}jobs failed: 2${nl}" "$OUT"
assert_eq 'failures: stderr' \
  "runjobs: job failed: copy no-such-file.txt${nl}runjobs: unknown action: shred${nl}" "$ERR"

# ---- 3. argument validation ---------------------------------------------------
run_jobs nope.tsv drop out3
assert_eq 'missing job list: exit code' '2' "$RC"
assert_eq 'missing job list: stderr' "runjobs: no such job list: nope.tsv${nl}" "$ERR"

# =================== REAL-WORLD NAMES (the rework contract) ===================

# ---- 4. names containing quote characters must just work ---------------------
printf -- '- ask about the retainer\n' > "$ROOT/$T/drop/client${sq}s notes.txt"
printf 'a "quoted" word\n'             > "$ROOT/$T/drop/press \"final\" cut.txt"

{
  printf 'copy\tclient%ss notes.txt\t\n' "$sq"
  printf 'pack\tclient%ss notes.txt\t\n' "$sq"
  printf 'note\tclient%ss notes.txt\t\n' "$sq"
  printf 'copy\tpress "final" cut.txt\t\n'
} > "$ROOT/$T/day4.tsv"

run_jobs day4.tsv drop out4
assert_eq 'quoted names: exit code' '0' "$RC"
assert_eq 'quoted names: stderr is quiet' '' "$ERR"
assert_eq 'quoted names: stdout' "jobs ok: 4${nl}jobs failed: 0${nl}" "$OUT"

checks=$((checks + 1))
if ! cmp -s "$ROOT/$T/drop/client${sq}s notes.txt" "$ROOT/$T/out4/client${sq}s notes.txt"; then
  fails=$((fails + 1)); printf 'FAIL quoted names: apostrophe file not copied intact\n'
fi
checks=$((checks + 1))
if ! cmp -s "$ROOT/$T/drop/press \"final\" cut.txt" "$ROOT/$T/out4/press \"final\" cut.txt"; then
  fails=$((fails + 1)); printf 'FAIL quoted names: double-quote file not copied intact\n'
fi

UNPACKED=$(gzip -dc "$ROOT/$T/out4/client${sq}s notes.txt.gz" 2>/dev/null || echo '(gunzip failed)')
assert_eq 'quoted names: pack output decompresses to the source' \
  '- ask about the retainer' "$UNPACKED"

LOG=''
slurp LOG "$ROOT/$T/out4/filing.log"
assert_eq 'quoted names: filing.log' "filed: client${sq}s notes.txt${nl}" "$LOG"

# ---- 5. commands are invoked directly, not assembled into strings ------------
checks=$((checks + 1))
if grep -Eq '(^|[^[:alnum:]_])eval([^[:alnum:]_]|$)' runjobs.sh; then
  fails=$((fails + 1))
  printf 'FAIL runjobs.sh still routes commands through eval\n'
fi

# ---- summary -----------------------------------------------------------------
if [[ $fails -gt 0 ]]; then
  printf '%d/%d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'ok - %d checks passed\n' "$checks"
