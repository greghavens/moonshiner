#!/usr/bin/env bash
# Acceptance harness for photosort.sh: the CI lint gate first, then
# behavior against a fixture card dump full of real-world filenames.

fails=0
note() { printf '%s\n' "$*"; }
fail() { fails=$((fails + 1)); printf 'FAIL: %s\n' "$*"; }

script_dir=$(cd "$(dirname "$0")" && pwd) || exit 1
cd "$script_dir" || exit 1

if ! command -v shellcheck >/dev/null 2>&1; then
  echo "FATAL: shellcheck is not on PATH; the lint gate cannot run" >&2
  exit 1
fi

# --- 1) CI lint gate: style severity, every finding counts -------------
if shellcheck -S style photosort.sh; then
  note "ok: shellcheck -S style is clean"
else
  fail "shellcheck -S style reports findings (see above)"
fi

# --- 2) a good card dump sorts, empties, and indexes --------------------
rm -rf fixture_root
mkdir -p "fixture_root/Card Dump"
library="$script_dir/fixture_root/Photo Library"
touch "fixture_root/Card Dump/2026-06-14 Beach Day 001.jpg"
touch "fixture_root/Card Dump/2026-06-14 Beach Day 002.jpg"
touch "fixture_root/Card Dump/2026-07-02 Marina Morning.png"
touch "fixture_root/Card Dump/screensaver copy.jpg"

(cd fixture_root && bash "$script_dir/photosort.sh" "Card Dump" "$library") \
  > fixture_root/out.txt 2> fixture_root/err.txt
status=$?
if [ $status -ne 0 ]; then
  fail "photosort exited $status on a good card dump: $(cat fixture_root/err.txt)"
fi

if ! grep -qxF 'sorted 4 file(s)' fixture_root/out.txt; then
  fail "summary line wrong; stdout was: $(cat fixture_root/out.txt)"
fi

for expect in \
  "Photo Library/2026-06/2026-06-14 Beach Day 001.jpg" \
  "Photo Library/2026-06/2026-06-14 Beach Day 002.jpg" \
  "Photo Library/2026-07/2026-07-02 Marina Morning.png" \
  "Photo Library/unsorted/screensaver copy.jpg"
do
  [ -f "fixture_root/$expect" ] || fail "not filed where expected: $expect"
done

leftover=$(find "fixture_root/Card Dump" -type f | wc -l)
[ "$leftover" -eq 0 ] || fail "card dump should be empty afterwards; $leftover file(s) left behind"

expected_index="2026-06/2026-06-14 Beach Day 001.jpg
2026-06/2026-06-14 Beach Day 002.jpg
2026-07/2026-07-02 Marina Morning.png
unsorted/screensaver copy.jpg"
actual_index=$(cat "$library/index/index.txt" 2>/dev/null)
if [ "$actual_index" != "$expected_index" ]; then
  fail "index mismatch
--- expected ---
$expected_index
--- actual ---
$actual_index"
fi

# --- 3) a missing card dir fails loudly and sorts nothing ---------------
rm -rf fixture_root/stray
mkdir fixture_root/stray
touch "fixture_root/stray/2026-01-01 Stray Frame.jpg"
(cd fixture_root/stray && bash "$script_dir/photosort.sh" "No Such Card" "$library") \
  >/dev/null 2>&1
status=$?
[ $status -ne 0 ] || fail "a missing card dir must exit nonzero"
[ -f "fixture_root/stray/2026-01-01 Stray Frame.jpg" ] \
  || fail "a missing card dir must not sort the directory the script was run from"

# --- 4) usage errors -----------------------------------------------------
bash photosort.sh >/dev/null 2>&1
[ $? -eq 64 ] || fail "no arguments should exit 64 with a usage line"

if [ "$fails" -eq 0 ]; then
  rm -rf fixture_root
  echo "ALL CHECKS PASSED"
  exit 0
fi
echo "$fails CHECK(S) FAILED"
exit 1
