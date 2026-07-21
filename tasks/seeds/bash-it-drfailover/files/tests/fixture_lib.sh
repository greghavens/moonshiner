#!/usr/bin/env bash

make_fixture() {
    local root=$1

    mkdir -p "$root/bin" "$root/fail" "$root/state/active"
    printf '%s\n' database cache api > "$root/services.list"
    : > "$root/events.log"

    cp "$FIXTURE_ADAPTERS/precheck" "$root/bin/precheck"
    cp "$FIXTURE_ADAPTERS/fence-primary" "$root/bin/fence-primary"
    cp "$FIXTURE_ADAPTERS/activate-service" "$root/bin/activate-service"
    cp "$FIXTURE_ADAPTERS/check-health" "$root/bin/check-health"
    cp "$FIXTURE_ADAPTERS/deactivate-service" "$root/bin/deactivate-service"
    chmod +x "$root/bin/precheck" "$root/bin/fence-primary" \
        "$root/bin/activate-service" "$root/bin/check-health" \
        "$root/bin/deactivate-service"
}

assert_status() {
    local expected=$1
    local label=$2
    if (( RUN_STATUS != expected )); then
        printf 'not ok - %s: expected status %s, got %s\n' \
            "$label" "$expected" "$RUN_STATUS" >&2
        return 1
    fi
}

assert_exists() {
    local path=$1
    local label=$2
    if [[ ! -e "$path" ]]; then
        printf 'not ok - %s: expected %s to exist\n' "$label" "$path" >&2
        return 1
    fi
}

assert_absent() {
    local path=$1
    local label=$2
    if [[ -e "$path" ]]; then
        printf 'not ok - %s: expected %s to be absent\n' "$label" "$path" >&2
        return 1
    fi
}

assert_empty_output() {
    local root=$1
    local label=$2
    if [[ -s "$root/stdout" || -s "$root/stderr" ]]; then
        printf 'not ok - %s: expected quiet operation\n' "$label" >&2
        sed 's/^/stdout: /' "$root/stdout" >&2
        sed 's/^/stderr: /' "$root/stderr" >&2
        return 1
    fi
}

assert_empty_file() {
    local path=$1
    local label=$2
    if [[ -s "$path" ]]; then
        printf 'not ok - %s: expected %s to be empty\n' "$label" "$path" >&2
        return 1
    fi
}

assert_lines() {
    local path=$1
    local expected=$2
    local label=$3
    local expected_file="$TEST_TMP/expected"

    printf '%s\n' "$expected" > "$expected_file"
    if ! cmp -s "$expected_file" "$path"; then
        printf 'not ok - %s: line sequence differs\n' "$label" >&2
        diff -u "$expected_file" "$path" >&2 || true
        return 1
    fi
}

run_failover() {
    local root=$1

    set +e
    bash "$PROJECT_ROOT/failover.sh" "$root" > "$root/stdout" 2> "$root/stderr"
    RUN_STATUS=$?
    set -e
}
