#!/usr/bin/env bash
set -u
export LC_ALL=C TZ=UTC

cd -- "$(dirname -- "$0")/.." || exit 1

failures=0
checks=0
work=.processpressure-test
rm -rf -- "$work"
mkdir -p -- "$work"
trap 'rm -rf -- "$work"' EXIT HUP INT TERM

fail() {
    printf 'not ok %d - %s\n' "$checks" "$1"
    failures=$((failures + 1))
}

pass() {
    printf 'ok %d - %s\n' "$checks" "$1"
}

expect_output() {
    local label=$1 expected=$2 actual status
    shift 2
    checks=$((checks + 1))
    actual=$("$@" 2>&1)
    status=$?
    if [[ $status -ne 0 ]]; then
        fail "$label (exit $status; output: $actual)"
    elif [[ "$actual" != "$expected" ]]; then
        fail "$label (unexpected output: $actual)"
    else
        pass "$label"
    fi
}

expect_file() {
    local label=$1 path=$2 expected=$3 actual
    checks=$((checks + 1))
    if [[ ! -f "$path" ]]; then
        fail "$label (missing $path)"
        return
    fi
    actual=$(< "$path")
    if [[ "$actual" != "$expected" ]]; then
        fail "$label (unexpected contents: $actual)"
    else
        pass "$label"
    fi
}

expect_same_file() {
    local label=$1 actual=$2 expected=$3
    checks=$((checks + 1))
    if cmp -s -- "$actual" "$expected"; then
        pass "$label"
    else
        fail "$label (files differ)"
    fi
}

expect_status() {
    local label=$1 expected=$2 actual
    shift 2
    checks=$((checks + 1))
    "$@" > "$work/status.stdout" 2> "$work/status.stderr"
    actual=$?
    if [[ $actual -eq $expected ]]; then
        pass "$label"
    else
        fail "$label (expected $expected, got $actual)"
    fi
}

expect_output 'CPU pressure is attributed to render.service' \
    $'state\tbefore\npressure\tcpu\nscope\trender.service\nculprit_pid\t1101\nobserved_pct\t87\nthreshold_pct\t80' \
    bash bin/processpressure diagnose fixtures/cpu-pressure

expect_output 'memory pressure is attributed to indexer.service' \
    $'state\tbefore\npressure\tmemory\nscope\tindexer.service\nculprit_pid\t2101\nobserved_pct\t95\nthreshold_pct\t80' \
    bash bin/processpressure diagnose fixtures/memory-pressure

expect_output 'descriptor pressure uses the process soft limit' \
    $'state\tbefore\npressure\tdescriptor\nscope\tgateway.service\nculprit_pid\t3101\nobserved_pct\t93\nthreshold_pct\t80' \
    bash bin/processpressure diagnose fixtures/descriptor-pressure

expect_output 'process pressure is counted within its service scope' \
    $'state\tbefore\npressure\tprocess-count\nscope\tworker.service\nculprit_pid\t4101\nobserved_pct\t80\nthreshold_pct\t80' \
    bash bin/processpressure diagnose fixtures/process-pressure

expect_output 'CPU after snapshot is healthy' \
    $'state\tafter\npressure\tnone\nscope\t-\nculprit_pid\t-\nobserved_pct\t0\nthreshold_pct\t80' \
    bash bin/processpressure diagnose fixtures/cpu-pressure after

expect_output 'memory after snapshot is healthy' \
    $'state\tafter\npressure\tnone\nscope\t-\nculprit_pid\t-\nobserved_pct\t0\nthreshold_pct\t80' \
    bash bin/processpressure diagnose fixtures/memory-pressure after

expect_output 'descriptor after snapshot is healthy' \
    $'state\tafter\npressure\tnone\nscope\t-\nculprit_pid\t-\nobserved_pct\t0\nthreshold_pct\t80' \
    bash bin/processpressure diagnose fixtures/descriptor-pressure after

expect_output 'process-count after snapshot is healthy' \
    $'state\tafter\npressure\tnone\nscope\t-\nculprit_pid\t-\nobserved_pct\t0\nthreshold_pct\t80' \
    bash bin/processpressure diagnose fixtures/process-pressure after

run_mitigation_test() {
    local fixture=$1 resource=$2 scope=$3 culprit=$4 before_pct=$5 after_pct=$6
    local action=$7 parameter=$8 value=$9
    local evidence="$work/$resource-evidence"

    expect_output "$resource mitigation is scoped and recovery is verified" \
        $'pressure\t'"$resource"$'\nscope\t'"$scope"$'\naction\t'"$action"$'\t'"$parameter"$'\t'"$value"$'\nrecovery\tverified\nevidence\t'"$evidence" \
        bash bin/processpressure mitigate "$fixture" "$evidence"

    expect_same_file "$resource retains its before process evidence" \
        "$evidence/before-processes.tsv" "$fixture/processes.before.tsv"
    expect_same_file "$resource retains its after process evidence" \
        "$evidence/after-processes.tsv" "$fixture/processes.after.tsv"
    expect_file "$resource records the simulated action" "$evidence/action.tsv" \
        $'action\tresource\tscope\tparameter\tvalue\n'"$action"$'\t'"$resource"$'\t'"$scope"$'\t'"$parameter"$'\t'"$value"
    expect_file "$resource records the supported decision" "$evidence/decision.tsv" \
        $'resource\tscope\tculprit_pid\tbefore_pct\tthreshold_pct\taction\tparameter\tvalue\n'"$resource"$'\t'"$scope"$'\t'"$culprit"$'\t'"$before_pct"$'\t80\t'"$action"$'\t'"$parameter"$'\t'"$value"
    expect_file "$resource records measured recovery" "$evidence/verification.tsv" \
        $'resource\tscope\tbefore_pct\tafter_pct\tthreshold_pct\tstatus\n'"$resource"$'\t'"$scope"$'\t'"$before_pct"$'\t'"$after_pct"$'\t80\trecovered'
}

run_mitigation_test fixtures/cpu-pressure cpu render.service 1101 87 40 \
    limit cpu.max_pct 200
run_mitigation_test fixtures/memory-pressure memory indexer.service 2101 95 35 \
    restart unit indexer.service
run_mitigation_test fixtures/descriptor-pressure descriptor gateway.service 3101 93 46 \
    limit nofile.soft 512
run_mitigation_test fixtures/process-pressure process-count worker.service 4101 80 30 \
    restart unit worker.service

expect_status 'an unknown subcommand is rejected' 64 \
    bash bin/processpressure inspect fixtures/cpu-pressure
expect_status 'an invalid snapshot name is rejected' 64 \
    bash bin/processpressure diagnose fixtures/cpu-pressure current

if (( failures > 0 )); then
    printf '1..%d\n' "$checks"
    printf '%d test(s) failed\n' "$failures" >&2
    exit 1
fi

printf '1..%d\n' "$checks"
