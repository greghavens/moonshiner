#!/usr/bin/env bash
# Acceptance harness for scratchprune.sh: the CI lint gate first, then the
# nightly-prune behavior against fixture scratch shares.
#
# Scenario 3 runs the script with a logging stand-in for rm on PATH so the
# robustness check can never delete anything, no matter how the script
# misbehaves. Do not remove that guard.

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
if shellcheck -S style scratchprune.sh; then
  note "ok: shellcheck -S style is clean"
else
  fail "shellcheck -S style reports findings (see above)"
fi

rm -rf fixture_root
mkdir -p fixture_root

# --- 2) a routine night: keep-list wins, spool is flushed ---------------
s1=fixture_root/night1
mkdir -p "$s1/proj-mothwing/data" "$s1/proj-kestrel" "$s1/shared-spool/render-cache"
printf 'x%.0s' {1..3000} > "$s1/proj-mothwing/data/results.bin"
printf 'notes\n' > "$s1/proj-kestrel/journal.txt"
printf 'frame\n' > "$s1/shared-spool/frame_0001.tmp"
printf 'frame\n' > "$s1/shared-spool/frame_0002.tmp"
printf 'held\n' > "$s1/shared-spool/.lockfile"
printf 'proj-mothwing\nproj-kestrel\n' > "$s1/retire.list"
printf 'proj-kestrel\n' > "$s1/keep.list"
printf 'label=night1\nspool=%s/shared-spool\n' "$s1" > "$s1/prune.conf"

bash scratchprune.sh "$s1" > fixture_root/out1.txt 2> fixture_root/err1.txt
status=$?
[ $status -eq 0 ] || fail "night1: expected exit 0, got $status"
[ -s fixture_root/err1.txt ] && fail "night1: stderr should be empty, got: $(cat fixture_root/err1.txt)"

grep -qx 'queued 2 project(s)' fixture_root/out1.txt || fail "night1: queued line wrong"
grep -qx 'keeping proj-kestrel' fixture_root/out1.txt || fail "night1: keep-list was not honored in the report"
grep -Eqx 'retired proj-mothwing [0-9]+KB' fixture_root/out1.txt || fail "night1: retired line missing or malformed"
grep -qx 'spool flushed 3 entries' fixture_root/out1.txt || fail "night1: spool count line wrong"
grep -qx 'done: 1 retired, 1 kept, 0 skipped' fixture_root/out1.txt || fail "night1: summary line wrong"

[ -d "$s1/proj-mothwing" ] && fail "night1: retired project directory is still there"
[ -f "$s1/proj-kestrel/journal.txt" ] || fail "night1: kept project lost its files"
[ -e "$s1/shared-spool/frame_0001.tmp" ] && fail "night1: spool entry survived the flush"
[ -e "$s1/shared-spool/render-cache" ] && fail "night1: spool subdirectory survived the flush"
[ -f "$s1/shared-spool/.lockfile" ] || fail "night1: the spool lockfile must survive a flush"
[ -d "$s1/shared-spool" ] || fail "night1: the spool directory itself must survive a flush"

# --- 3) a listed project whose directory is already gone ----------------
s2=fixture_root/night2
mkdir -p "$s2/proj-brill" "$s2/shared-spool"
printf 'data\n' > "$s2/proj-brill/notes.txt"
printf 'junk\n' > "$s2/shared-spool/old.tmp"
printf 'proj-brill\nproj-ghost\n' > "$s2/retire.list"
: > "$s2/keep.list"
printf 'spool=%s/shared-spool\n' "$s2" > "$s2/prune.conf"

bash scratchprune.sh "$s2" > fixture_root/out2.txt 2> fixture_root/err2.txt
status=$?
[ $status -eq 3 ] || fail "night2: a skipped project must be reported via exit 3, got $status"
grep -qx 'warning: cannot size proj-ghost, skipped' fixture_root/err2.txt \
  || fail "night2: missing-directory warning not on stderr; stderr was: $(cat fixture_root/err2.txt)"
grep -q 'retired proj-ghost' fixture_root/out2.txt && fail "night2: a directory that could not be sized was reported as retired"
grep -Eqx 'retired proj-brill [0-9]+KB' fixture_root/out2.txt || fail "night2: healthy project was not retired"
grep -qx 'done: 1 retired, 0 kept, 1 skipped' fixture_root/out2.txt || fail "night2: summary must count the skip"

# --- 4) prune.conf without a spool= entry must stop before any rm -------
# The stub rm records what would have been deleted and deletes nothing.
s3=fixture_root/night3
mkdir -p "$s3/proj-canary" fixture_root/stubbin
printf 'precious\n' > "$s3/proj-canary/data.txt"
: > "$s3/retire.list"
: > "$s3/keep.list"
printf 'label=night3\n' > "$s3/prune.conf"
cat > fixture_root/stubbin/rm <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" >> "${RM_LOG:?}"
exit 0
STUB
chmod +x fixture_root/stubbin/rm
rm -f fixture_root/rm3.log
(
  export RM_LOG="$script_dir/fixture_root/rm3.log"
  export PATH="$script_dir/fixture_root/stubbin:$PATH"
  bash scratchprune.sh "$s3" > "$script_dir/fixture_root/out3.txt" 2> "$script_dir/fixture_root/err3.txt"
)
status=$?
[ $status -ne 0 ] || fail "night3: with no spool= configured the script must refuse to flush (nonzero exit)"
if [ -s fixture_root/rm3.log ]; then
  fail "night3: something was handed to rm with no spool configured: $(head -n 3 fixture_root/rm3.log | tr '\n' ' ')"
fi
[ -f "$s3/proj-canary/data.txt" ] || fail "night3: canary file went missing"

# --- verdict -------------------------------------------------------------
if [ "$fails" -eq 0 ]; then
  rm -rf fixture_root
  echo "ALL CHECKS PASSED"
  exit 0
fi
echo "$fails CHECK(S) FAILED"
exit 1
