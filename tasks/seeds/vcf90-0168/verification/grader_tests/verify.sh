#!/usr/bin/env bash
set -euo pipefail

seed_root=$(cd "$(dirname "$0")/.." && pwd)
verify_tmp=$(mktemp -d)
mock_pid=""
cleanup() {
    if [[ -n "$mock_pid" ]]; then
        kill "$mock_pid" 2>/dev/null || true
        wait "$mock_pid" 2>/dev/null || true
    fi
    rm -rf "$verify_tmp"
}
trap cleanup EXIT

javac -encoding UTF-8 -d "$verify_tmp/classes" \
    "$seed_root/VcfAutomationClient.java" \
    "$seed_root/grader_tests/TestMain.java"

run_scenario() {
    local scenario=$1
    local scenario_dir="$verify_tmp/$scenario"
    mkdir -p "$scenario_dir"

    python3 "$seed_root/mock/vcf_automation_mock.py" \
        --contract "$seed_root/docs/contract.json" \
        --log "$scenario_dir/requests.log" \
        --port-file "$scenario_dir/port" \
        --scenario "$scenario" &
    mock_pid=$!

    for _ in $(seq 1 100); do
        [[ -s "$scenario_dir/port" ]] && break
        sleep 0.02
    done
    [[ -s "$scenario_dir/port" ]]
    local mock_port
    mock_port=$(<"$scenario_dir/port")

    java -cp "$verify_tmp/classes" TestMain \
        "http://127.0.0.1:$mock_port" \
        "$scenario_dir/requests.log" \
        "$scenario"

    kill "$mock_pid" 2>/dev/null || true
    wait "$mock_pid" 2>/dev/null || true
    mock_pid=""
}

for scenario in \
    final-failure \
    full-success \
    project-failure \
    zone-failure \
    null-collections \
    empty-collections; do
    run_scenario "$scenario"
done
