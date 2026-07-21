#!/usr/bin/env bash

set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
checker="$root/bin/incidentcheck"
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT HUP INT TERM

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

assert_file() {
    expected=$1
    actual=$2
    label=$3
    if ! diff -u "$expected" "$actual"; then
        fail "$label"
    fi
}

make_shims() {
    scenario=$1
    mkdir -p "$scenario"

    cat > "$scenario/health" <<'SH'
#!/usr/bin/env bash
set -u
count_file=$INCIDENTCHECK_FIXTURE/count
sequence_file=$INCIDENTCHECK_FIXTURE/sequence
count=0
if [[ -f $count_file ]]; then
    IFS= read -r count < "$count_file"
fi
(( count += 1 ))
printf '%d\n' "$count" > "$count_file"
status=$(sed -n "${count}p" "$sequence_file")
if [[ -z $status ]]; then
    status=1
fi
exit "$status"
SH

    cat > "$scenario/sleep" <<'SH'
#!/usr/bin/env bash
set -u
printf '%s\n' "$1" >> "$INCIDENTCHECK_FIXTURE/sleeps"
SH
    chmod +x "$scenario/health" "$scenario/sleep"
}

run_check() {
    scenario=$1
    interval=$2
    timeout=$3
    successes=$4
    evidence=$5
    INCIDENTCHECK_FIXTURE=$scenario bash "$checker" \
        --interval "$interval" \
        --timeout "$timeout" \
        --successes "$successes" \
        --evidence "$evidence" \
        --sleep-command "$scenario/sleep" \
        -- "$scenario/health"
}

# A never-healthy shim proves that immediate-return interval fixtures cannot
# cause unbounded polling and that the final interval is capped at the deadline.
timeout_case="$tmp/timeout"
make_shims "$timeout_case"
printf '7\n7\n7\n7\n7\n' > "$timeout_case/sequence"
set +e
run_check "$timeout_case" 2 5 2 "$timeout_case/evidence.tsv"
status=$?
set -e
[[ $status -eq 1 ]] || fail "timeout exit status was $status, expected 1"
[[ $(<"$timeout_case/count") == 4 ]] || fail 'timeout loop used the wrong probe count'
printf '2\n2\n1\n' > "$timeout_case/expected-sleeps"
assert_file "$timeout_case/expected-sleeps" "$timeout_case/sleeps" \
    'polling intervals were not capped at the timeout boundary'
cat > "$timeout_case/expected-evidence.tsv" <<'EOF'
event	sample	elapsed_seconds	status	exit_code	streak
probe	1	0	unhealthy	7	0
probe	2	2	unhealthy	7	0
probe	3	4	unhealthy	7	0
probe	4	5	unhealthy	7	0
result	-	5	timeout	1	0
EOF
assert_file "$timeout_case/expected-evidence.tsv" "$timeout_case/evidence.tsv" \
    'timeout evidence did not record every observation'

# A green sample between failures is transient. Only the final two adjacent
# healthy samples satisfy this policy.
recovery_case="$tmp/recovery"
make_shims "$recovery_case"
printf '9\n0\n4\n0\n0\n' > "$recovery_case/sequence"
run_check "$recovery_case" 3 12 2 "$recovery_case/evidence.tsv" || \
    fail 'a verified recovery did not exit successfully'
[[ $(<"$recovery_case/count") == 5 ]] || \
    fail 'checker declared recovery before two consecutive healthy samples'
printf '3\n3\n3\n3\n' > "$recovery_case/expected-sleeps"
assert_file "$recovery_case/expected-sleeps" "$recovery_case/sleeps" \
    'recovery polling did not honor the configured interval'
cat > "$recovery_case/expected-evidence.tsv" <<'EOF'
event	sample	elapsed_seconds	status	exit_code	streak
probe	1	0	unhealthy	9	0
probe	2	3	healthy	0	1
probe	3	6	unhealthy	4	0
probe	4	9	healthy	0	1
probe	5	12	healthy	0	2
result	-	12	recovered	0	2
EOF
assert_file "$recovery_case/expected-evidence.tsv" "$recovery_case/evidence.tsv" \
    'recovery evidence did not preserve the streak history'

# A one-success policy should still return on its first healthy observation.
single_case="$tmp/single"
make_shims "$single_case"
printf '0\n' > "$single_case/sequence"
run_check "$single_case" 4 8 1 "$single_case/evidence.tsv" || \
    fail 'single-success policy did not recover'
[[ $(<"$single_case/count") == 1 ]] || fail 'single-success policy over-polled'
[[ ! -e $single_case/sleeps ]] || fail 'checker slept after reaching policy'

printf 'PASS: incident verification is bounded and requires consecutive health\n'
