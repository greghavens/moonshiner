#!/usr/bin/env zsh
# Acceptance harness for frostwatch.zsh. The greenhouse cron host runs every
# zsh script with warn_create_global and no_unset switched on, so the report
# must come out byte-identical and with a completely silent stderr under
# those options — and behave the same without them.

fails=0
note() { print -r -- "$1" }
fail() { (( fails += 1 )); print -r -- "FAIL: $1" }

script_dir=${0:A:h}
cd "$script_dir" || exit 1

unset FROST_LIMIT

# --- 1) the script must at least parse ----------------------------------
if zsh -n frostwatch.zsh; then
  note "ok: zsh -n accepts the script"
else
  fail "zsh -n rejects the script"
fi

# --- 1b) the fix must be real: no switching the host's options back off ----
if grep -nE '\b(un)?setopt\b|\bemulate\b' frostwatch.zsh; then
  fail "frostwatch.zsh must not fiddle with shell options (see lines above); fix the variables instead"
else
  note "ok: script leaves the host's options alone"
fi

rm -rf fixture_root
mkdir -p fixture_root
printf 'bed-A\t1\nbed-B\t3\nbed-A\t-2\nbed-C\t0\nbed-B\t5\n' > fixture_root/templog.tsv

run_diff() {
  # run_diff LABEL EXPECT_FILE EXPECT_STATUS OUT_FILE ERR_FILE STATUS
  local label=$1 expect=$2 want=$3 out=$4 err=$5 got=$6
  if (( got != want )); then
    fail "$label: expected exit $want, got $got"
  fi
  if [[ -s $err ]]; then
    fail "$label: stderr must be silent, got: $(<$err)"
  fi
  if ! diff -u "$expect" "$out" > fixture_root/last_diff.txt; then
    fail "$label: report differs:
$(<fixture_root/last_diff.txt)"
  fi
}

# --- 2) default limit, strict options, FROST_LIMIT genuinely unset -------
cat > fixture_root/expect_a.txt <<'REPORT'
frost report (limit 0C)
bed-A min -2 FROST
bed-B min 3 ok
bed-C min 0 ok
checked 3 bed(s), 1 frosty
REPORT
zsh -o warn_create_global -o no_unset frostwatch.zsh fixture_root/templog.tsv \
  > fixture_root/out_a.txt 2> fixture_root/err_a.txt
run_diff "night A (strict, defaults)" fixture_root/expect_a.txt 1 \
  fixture_root/out_a.txt fixture_root/err_a.txt $?

# --- 3) mild night: limit lowered via the environment ---------------------
cat > fixture_root/expect_b.txt <<'REPORT'
frost report (limit -5C)
bed-A min -2 ok
bed-B min 3 ok
bed-C min 0 ok
checked 3 bed(s), 0 frosty
REPORT
FROST_LIMIT=-5 zsh -o warn_create_global -o no_unset frostwatch.zsh fixture_root/templog.tsv \
  > fixture_root/out_b.txt 2> fixture_root/err_b.txt
run_diff "night B (strict, limit -5)" fixture_root/expect_b.txt 0 \
  fixture_root/out_b.txt fixture_root/err_b.txt $?

# --- 4) single-bed report with a raised limit ------------------------------
cat > fixture_root/expect_c.txt <<'REPORT'
frost report (limit 4C)
bed-B min 3 FROST
checked 1 bed(s), 1 frosty
REPORT
FROST_LIMIT=4 zsh -o warn_create_global -o no_unset frostwatch.zsh fixture_root/templog.tsv bed-B \
  > fixture_root/out_c.txt 2> fixture_root/err_c.txt
run_diff "night C (strict, bed filter)" fixture_root/expect_c.txt 1 \
  fixture_root/out_c.txt fixture_root/err_c.txt $?

# --- 5) and identically without the strict options -------------------------
zsh frostwatch.zsh fixture_root/templog.tsv \
  > fixture_root/out_d.txt 2> fixture_root/err_d.txt
run_diff "night D (plain zsh, defaults)" fixture_root/expect_a.txt 1 \
  fixture_root/out_d.txt fixture_root/err_d.txt $?

# --- verdict ---------------------------------------------------------------
if (( fails == 0 )); then
  rm -rf fixture_root
  print "ALL CHECKS PASSED"
  exit 0
fi
print "$fails CHECK(S) FAILED"
exit 1
