#!/usr/bin/env bash
# Regression harness for snapshot.sh (month-end export filing).
# Run from the workspace root:  bash test_ledgersnap.sh
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
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

slurp() { # slurp <var> <file> -- byte-exact file contents into var
  IFS= read -r -d '' "$1" < "$2" || true
}

RC=0
OUT=''
ERR=''
run_snapshot() { # run_snapshot <args...> -- capture RC, OUT, ERR byte-exactly
  ( cd "$ROOT/$T" && exec bash "$ROOT/snapshot.sh" "$@" ) \
    > "$ROOT/$T/.out" 2> "$ROOT/$T/.err"
  RC=$?
  slurp OUT "$ROOT/$T/.out"
  slurp ERR "$ROOT/$T/.err"
}

if [[ ! -f snapshot.sh ]]; then
  printf 'FAIL snapshot.sh not found in the workspace root\n'
  exit 1
fi

nl=$'\n'
tab=$'\t'

# ---- fixture: a drop folder full of real-world filenames --------------------
# names and contents, kept in parallel arrays so expectations can be computed
names=(
  'readme.txt'
  'Monthly Report $Q3.txt'
  'budget (final) v2.csv'
  'notes [draft].md'
  'year  end summary.txt'
)
contents=(
  'ledger drop for the quarter'$'\n'
  'q3 totals: 41,220.55'$'\n'
  'line,amount'$'\n''rent,950'$'\n'
  '- carry forward the deposit'$'\n'
  'pending review'$'\n'
)

make_drop() { # make_drop <dir>
  local d=$1 i
  mkdir -p "$d"
  for i in "${!names[@]}"; do
    printf '%s' "${contents[$i]}" > "$d/${names[$i]}"
  done
  # subdirectories are other teams' business and must stay behind
  mkdir -p "$d/old exports"
  printf 'not ours\n' > "$d/old exports/keep.txt"
}

# ---- 1. happy path: every loose file is copied, manifested, verified --------
make_drop "$ROOT/$T/drop"
run_snapshot drop snaps 2026-06

assert_eq 'happy path: exit code' '0' "$RC"
assert_eq 'happy path: stderr is quiet' '' "$ERR"
assert_eq 'happy path: stdout' \
  "copied 5 file(s) into snaps/2026-06${nl}verify ok${nl}" "$OUT"

snap="$ROOT/$T/snaps/2026-06"
for i in "${!names[@]}"; do
  n=${names[$i]}
  checks=$((checks + 1))
  if [[ -f "$snap/$n" ]] && cmp -s "$ROOT/$T/drop/$n" "$snap/$n"; then
    :
  else
    fails=$((fails + 1))
    printf 'FAIL happy path: %s missing or altered in the snapshot\n' "$n"
  fi
done

checks=$((checks + 1))
if [[ -e "$snap/old exports" || -e "$snap/keep.txt" ]]; then
  fails=$((fails + 1))
  printf 'FAIL happy path: subdirectory contents leaked into the snapshot\n'
fi

# exactly the five files plus MANIFEST.tsv, nothing else (no stray fragments)
entry_count=$(find "$snap" -mindepth 1 | wc -l)
assert_eq 'happy path: snapshot entry count' '6' "$(echo $entry_count)"

expected_manifest=''
while IFS= read -r line; do
  expected_manifest+="$line$nl"
done < <(
  for i in "${!names[@]}"; do
    printf '%s\t%s\n' "${names[$i]}" "${#contents[$i]}"
  done | sort
)
MAN=''
slurp MAN "$snap/MANIFEST.tsv"
assert_eq 'happy path: MANIFEST.tsv' "$expected_manifest" "$MAN"

# ---- 2. refuses to overwrite an existing snapshot ----------------------------
run_snapshot drop snaps 2026-06
assert_eq 'existing label: exit code' '2' "$RC"
assert_eq 'existing label: stdout' '' "$OUT"
assert_eq 'existing label: stderr' \
  "snapshot: already exists, refusing to overwrite: snaps/2026-06${nl}" "$ERR"

# ---- 3. a fresh label re-runs cleanly ----------------------------------------
run_snapshot drop snaps 2026-06-redo
assert_eq 'fresh label: exit code' '0' "$RC"
assert_eq 'fresh label: stderr is quiet' '' "$ERR"
assert_eq 'fresh label: stdout' \
  "copied 5 file(s) into snaps/2026-06-redo${nl}verify ok${nl}" "$OUT"

# ---- 4. missing source directory ---------------------------------------------
run_snapshot no-such-drop snaps 2026-07
assert_eq 'missing source: exit code' '2' "$RC"
assert_eq 'missing source: stderr' \
  "snapshot: source directory not found: no-such-drop${nl}" "$ERR"

# ---- 5. empty drop folder: zero copied, empty manifest, verify ok ------------
mkdir -p "$ROOT/$T/empty"
run_snapshot empty snaps 2026-08
assert_eq 'empty drop: exit code' '0' "$RC"
assert_eq 'empty drop: stderr is quiet' '' "$ERR"
assert_eq 'empty drop: stdout' \
  "copied 0 file(s) into snaps/2026-08${nl}verify ok${nl}" "$OUT"
EMPTYMAN='x'
slurp EMPTYMAN "$ROOT/$T/snaps/2026-08/MANIFEST.tsv"
assert_eq 'empty drop: MANIFEST.tsv is empty' '' "$EMPTYMAN"

# ---- summary -----------------------------------------------------------------
if [[ $fails -gt 0 ]]; then
  printf '%d/%d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'ok - %d checks passed\n' "$checks"
