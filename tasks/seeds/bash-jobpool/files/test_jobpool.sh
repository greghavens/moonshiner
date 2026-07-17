#!/usr/bin/env bash
# Acceptance harness for pool.sh (bounded parallel job runner).
# Run from the workspace root:  bash test_jobpool.sh
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

assert_present() { # assert_present <label> <path>
  checks=$((checks + 1))
  if [[ -e "$2" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (missing: %s)\n' "$1" "$2"
}

assert_absent() { # assert_absent <label> <path>
  checks=$((checks + 1))
  if [[ ! -e "$2" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (still exists: %s)\n' "$1" "$2"
}

assert_le() { # assert_le <label> <actual> <bound>
  checks=$((checks + 1))
  if [[ "$2" -le "$3" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (%s > %s)\n' "$1" "$2" "$3"
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

# wait (bounded, ~10s) for a path to appear; FAIL the case if it never does
wait_for() { # wait_for <label> <path>
  local n=0
  while [[ ! -e "$2" && $n -lt 200 ]]; do
    sleep 0.05
    n=$((n + 1))
  done
  checks=$((checks + 1))
  if [[ -e "$2" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (timed out waiting for %s)\n' "$1" "$2"
  return 1
}

if [[ ! -f pool.sh ]]; then
  printf 'FAIL pool.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

nl=$'\n'
tab=$'\t'

# ---- argument validation -------------------------------------------------------

mkdir -p "$T/wd_a"
printf 'noop\ttrue\n' > "$T/wd_a/jobs.txt"
printf -v exp_usage 'usage: pool.sh <manifest> <max-workers>\n'

run_in "$T/wd_a" bash "$ROOT/pool.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T/wd_a" bash "$ROOT/pool.sh" jobs.txt
expect "missing worker count" 64 "" "$exp_usage"

run_in "$T/wd_a" bash "$ROOT/pool.sh" jobs.txt 0
expect "zero workers" 64 "" "$exp_usage"

run_in "$T/wd_a" bash "$ROOT/pool.sh" jobs.txt two
expect "non-numeric workers" 64 "" "$exp_usage"

run_in "$T/wd_a" bash "$ROOT/pool.sh" absent.txt 2
expect "unreadable manifest" 66 "" "pool.sh: cannot read: absent.txt$nl"

# ---- manifest validation happens before anything runs ---------------------------

mkdir -p "$T/wd_b"
{
  printf '# maintenance batch\n'
  printf 'ok\t: > ran.marker\n'
  printf 'no tab here\n'
  printf 'ok\techo again\n'
  printf '\techo no name\n'
  printf '\n'
  printf 'we b\techo bad name\n'
  printf 'trail\t\n'
} > "$T/wd_b/jobs.txt"

printf -v exp_bad_err 'pool.sh: line 3: malformed\npool.sh: line 4: duplicate job name: ok\npool.sh: line 5: malformed\npool.sh: line 7: malformed\npool.sh: line 8: malformed\n'
run_in "$T/wd_b" bash "$ROOT/pool.sh" jobs.txt 2
expect "malformed manifest" 65 "" "$exp_bad_err"
assert_absent "malformed manifest: no job ran" "$T/wd_b/ran.marker"
assert_absent "malformed manifest: no results file" "$T/wd_b/results.txt"
assert_absent "malformed manifest: no logs dir" "$T/wd_b/logs"

# ---- mixed batch: logs, results, aggregate status --------------------------------

mkdir -p "$T/wd_c/jobs"
printf '#!/usr/bin/env bash\nprintf "starting one\\n"\nprintf "done one\\n"\n' > "$T/wd_c/jobs/ok_one.sh"
printf '#!/usr/bin/env bash\nprintf "out line\\n"\nprintf "err line\\n" >&2\nprintf "out again\\n"\n' > "$T/wd_c/jobs/noisy.sh"
printf '#!/usr/bin/env bash\nprintf "attempt failed\\n" >&2\nexit 3\n' > "$T/wd_c/jobs/flaky_exit.sh"
printf '#!/usr/bin/env bash\nexit 0\n' > "$T/wd_c/jobs/quiet.sh"
{
  printf '# nightly maintenance\n'
  printf 'ok_one\tbash jobs/ok_one.sh\n'
  printf 'noisy\tbash jobs/noisy.sh\n'
  printf 'flaky_exit\tbash jobs/flaky_exit.sh\n'
  printf '\n'
  printf 'quiet\tbash jobs/quiet.sh\n'
  printf "tabby\tprintf '%%s\\\\n' 'A\tB'\n"
} > "$T/wd_c/jobs.txt"

run_in "$T/wd_c" bash "$ROOT/pool.sh" jobs.txt 2
expect "mixed batch" 1 "pool: 5 jobs, 1 failed$nl" ""

SORTED=''
LC_ALL=C sort "$T/wd_c/results.txt" > "$T/results.sorted" 2>/dev/null || : > "$T/results.sorted"
slurp SORTED "$T/results.sorted"
printf -v exp_results 'flaky_exit\t3\nnoisy\t0\nok_one\t0\nquiet\t0\ntabby\t0\n'
assert_eq "mixed batch: results manifest (sorted)" "$exp_results" "$SORTED"

L=''
slurp L "$T/wd_c/logs/ok_one.log"
assert_eq "mixed batch: ok_one log" "starting one${nl}done one$nl" "$L"
slurp L "$T/wd_c/logs/noisy.log"
assert_eq "mixed batch: noisy log keeps stream order" "out line${nl}err line${nl}out again$nl" "$L"
slurp L "$T/wd_c/logs/flaky_exit.log"
assert_eq "mixed batch: failing job log" "attempt failed$nl" "$L"
assert_present "mixed batch: quiet log exists" "$T/wd_c/logs/quiet.log"
slurp L "$T/wd_c/logs/quiet.log"
assert_eq "mixed batch: quiet log empty" "" "$L"
slurp L "$T/wd_c/logs/tabby.log"
assert_eq "mixed batch: command split at first tab only" "A${tab}B$nl" "$L"

# ---- all-success batch, stale output replaced, workers > jobs ---------------------

mkdir -p "$T/wd_d/logs"
printf 'stale log line\n' > "$T/wd_d/logs/one.log"
printf 'stale\t9\n' > "$T/wd_d/results.txt"
{
  printf 'one\tprintf "fresh one\\n"\n'
  printf 'two\tprintf "fresh two\\n"\n'
} > "$T/wd_d/jobs.txt"

run_in "$T/wd_d" bash "$ROOT/pool.sh" jobs.txt 5
expect "all-success batch" 0 "pool: 2 jobs, 0 failed$nl" ""
LC_ALL=C sort "$T/wd_d/results.txt" > "$T/results.sorted"
slurp SORTED "$T/results.sorted"
printf -v exp_results_d 'one\t0\ntwo\t0\n'
assert_eq "all-success batch: results rewritten fresh" "$exp_results_d" "$SORTED"
slurp L "$T/wd_d/logs/one.log"
assert_eq "all-success batch: stale log replaced" "fresh one$nl" "$L"

# ---- empty manifest ----------------------------------------------------------------

mkdir -p "$T/wd_f"
printf '# nothing scheduled\n\n' > "$T/wd_f/jobs.txt"
run_in "$T/wd_f" bash "$ROOT/pool.sh" jobs.txt 3
expect "empty manifest" 0 "pool: 0 jobs, 0 failed$nl" ""
assert_present "empty manifest: results file created" "$T/wd_f/results.txt"
slurp L "$T/wd_f/results.txt"
assert_eq "empty manifest: results empty" "" "$L"

# ---- concurrency bound: gated jobs force overlap and pin the ceiling ---------------

mkdir -p "$T/wd_e/jobs" "$T/wd_e/running" "$T/wd_e/started" "$T/wd_e/obs" "$T/wd_e/release"
cat > "$T/wd_e/jobs/gate.sh" <<'GATE'
#!/usr/bin/env bash
# gate job: mark running, record observed concurrency, then hold until released
name=$1
: > "running/$name"
c=0
for f in running/*; do
  [[ -e "$f" ]] && c=$((c + 1))
done
printf '%s\n' "$c" > "obs/$name"
: > "started/$name"
n=0
while [[ ! -e "release/$name" && $n -lt 400 ]]; do
  sleep 0.05
  n=$((n + 1))
done
rm -f "running/$name"
exit 0
GATE
{
  printf 'g1\tbash jobs/gate.sh g1\n'
  printf 'g2\tbash jobs/gate.sh g2\n'
  printf 'g3\tbash jobs/gate.sh g3\n'
} > "$T/wd_e/jobs.txt"

( cd "$T/wd_e" && exec bash "$ROOT/pool.sh" jobs.txt 2 ) \
    > "$ROOT/$T/pool.out" 2> "$ROOT/$T/pool.err" &
pool_pid=$!

ok_spinup=1
wait_for "bound: first job started" "$T/wd_e/started/g1" || ok_spinup=0
wait_for "bound: second job started" "$T/wd_e/started/g2" || ok_spinup=0
if [[ "$ok_spinup" -eq 1 ]]; then
  # both slots are held and neither job has been released: with a ceiling of
  # 2 the third job cannot have started yet
  assert_absent "bound: third job waits for a free slot" "$T/wd_e/started/g3"
  : > "$T/wd_e/release/g1"
  wait_for "bound: third job starts after a slot frees" "$T/wd_e/started/g3" || true
fi
# release everything so the pool can finish even if an assert above failed
: > "$T/wd_e/release/g1"
: > "$T/wd_e/release/g2"
: > "$T/wd_e/release/g3"
wait "$pool_pid"
RC=$?
slurp OUT "$ROOT/$T/pool.out"
slurp ERR "$ROOT/$T/pool.err"
expect "bound: gated batch completes" 0 "pool: 3 jobs, 0 failed$nl" ""

obs_max=0
for j in g1 g2 g3; do
  v=0
  [[ -f "$T/wd_e/obs/$j" ]] && read -r v < "$T/wd_e/obs/$j"
  assert_le "bound: observed concurrency for $j stays within ceiling" "$v" 2
  [[ "$v" -gt "$obs_max" ]] && obs_max=$v
done
assert_eq "bound: two jobs really did overlap" 2 "$obs_max"
v=0
[[ -f "$T/wd_e/obs/g3" ]] && read -r v < "$T/wd_e/obs/g3"
assert_eq "bound: queued job ran alongside the remaining holder" 2 "$v"
LC_ALL=C sort "$T/wd_e/results.txt" > "$T/results.sorted"
slurp SORTED "$T/results.sorted"
printf -v exp_results_e 'g1\t0\ng2\t0\ng3\t0\n'
assert_eq "bound: gated results manifest" "$exp_results_e" "$SORTED"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
