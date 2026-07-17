#!/usr/bin/env bash
# Acceptance harness for feedmix.sh: CI lint gate, then the prep sheet is
# compared byte-for-byte against what the barn crew signs off on.

fails=0
note() { printf '%s\n' "$*"; }
fail() { fails=$((fails + 1)); printf 'FAIL: %s\n' "$*"; }

script_dir=$(cd "$(dirname "$0")" && pwd) || exit 1
cd "$script_dir" || exit 1

if ! command -v shellcheck >/dev/null 2>&1; then
  echo "FATAL: shellcheck is not on PATH; the lint gate cannot run" >&2
  exit 1
fi

# --- 1) CI lint gate: style severity, zero findings allowed ------------
if shellcheck -S style feedmix.sh; then
  note "ok: shellcheck -S style is clean"
else
  fail "shellcheck -S style reports findings (see above)"
fi

rm -rf fixture_root
mkdir -p fixture_root

cat > fixture_root/chart.txt <<'CHART'
Duke:2:rolled oats:flax:
Willow:1:bran mash::50\50 with hot water
Bramble:1:pellets:sea salt:
Pip:3:pellets:salt:
Sundance:2:sweet feed:beet pulp:warm the molasses
CHART

# --- 2) morning round with a partly stocked bin room --------------------
cat > fixture_root/expect_a.txt <<'SHEET'
Duke | 2 scoops | rolled oats | plus flax
Willow | 1 scoops | bran mash | note: 50\50 with hot water
Bramble | 1 scoops | pellets | plus sea salt
Pip | 3 scoops | pellets | plus salt (NOT IN BIN ROOM)
Sundance | 2 scoops | sweet feed | plus beet pulp (NOT IN BIN ROOM) | note: warm the molasses
fed 5 horse(s)
short on: salt beet pulp
SHEET

bash feedmix.sh fixture_root/chart.txt "sea salt" flax \
  > fixture_root/out_a.txt 2> fixture_root/err_a.txt
status=$?
[ $status -eq 3 ] || fail "round A: missing supplements must exit 3, got $status"
[ -s fixture_root/err_a.txt ] && fail "round A: stderr should be empty, got: $(cat fixture_root/err_a.txt)"
if ! diff -u fixture_root/expect_a.txt fixture_root/out_a.txt > fixture_root/diff_a.txt; then
  fail "round A: prep sheet differs from the signed-off sheet:
$(cat fixture_root/diff_a.txt)"
fi

# --- 3) fully stocked bin room ------------------------------------------
cat > fixture_root/expect_b.txt <<'SHEET'
Duke | 2 scoops | rolled oats | plus flax
Willow | 1 scoops | bran mash | note: 50\50 with hot water
Bramble | 1 scoops | pellets | plus sea salt
Pip | 3 scoops | pellets | plus salt
Sundance | 2 scoops | sweet feed | plus beet pulp | note: warm the molasses
fed 5 horse(s)
SHEET

bash feedmix.sh fixture_root/chart.txt flax "sea salt" salt "beet pulp" \
  > fixture_root/out_b.txt 2> fixture_root/err_b.txt
status=$?
[ $status -eq 0 ] || fail "round B: fully stocked round must exit 0, got $status"
[ -s fixture_root/err_b.txt ] && fail "round B: stderr should be empty, got: $(cat fixture_root/err_b.txt)"
if ! diff -u fixture_root/expect_b.txt fixture_root/out_b.txt > fixture_root/diff_b.txt; then
  fail "round B: prep sheet differs from the signed-off sheet:
$(cat fixture_root/diff_b.txt)"
fi

# --- 4) unreadable chart: one tidy line on stderr ------------------------
bash feedmix.sh fixture_root/no-such-chart.txt \
  > fixture_root/out_c.txt 2> fixture_root/err_c.txt
status=$?
[ $status -eq 2 ] || fail "round C: missing chart must exit 2, got $status"
[ -s fixture_root/out_c.txt ] && fail "round C: nothing should be printed to stdout"
printf 'feedmix: cannot read chart fixture_root/no-such-chart.txt\n' > fixture_root/expect_c.txt
if ! diff -u fixture_root/expect_c.txt fixture_root/err_c.txt > fixture_root/diff_c.txt; then
  fail "round C: the error must be exactly one tidy line:
$(cat fixture_root/diff_c.txt)"
fi

# --- 5) usage message stays on one line ----------------------------------
bash feedmix.sh > fixture_root/out_d.txt 2> fixture_root/err_d.txt
status=$?
[ $status -eq 2 ] || fail "round D: no arguments must exit 2, got $status"
printf 'usage: feedmix.sh CHART [supplement ...]\n' > fixture_root/expect_d.txt
if ! diff -u fixture_root/expect_d.txt fixture_root/err_d.txt > fixture_root/diff_d.txt; then
  fail "round D: usage must be exactly one line:
$(cat fixture_root/diff_d.txt)"
fi

# --- verdict -------------------------------------------------------------
if [ "$fails" -eq 0 ]; then
  rm -rf fixture_root
  echo "ALL CHECKS PASSED"
  exit 0
fi
echo "$fails CHECK(S) FAILED"
exit 1
