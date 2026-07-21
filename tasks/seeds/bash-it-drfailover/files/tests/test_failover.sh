#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TEST_TMP=$(mktemp -d "${TMPDIR:-/tmp}/drfailover-test.XXXXXX")
trap 'rm -rf -- "$TEST_TMP"' EXIT

FIXTURE_ADAPTERS="$TEST_TMP/adapters"
mkdir -p "$FIXTURE_ADAPTERS"

cat > "$FIXTURE_ADAPTERS/precheck" <<'ADAPTER'
#!/usr/bin/env bash
set -u
root=$1
check=$2
printf 'precheck %s\n' "$check" >> "$root/events.log"
[[ ! -e "$root/fail/precheck-$check" ]]
ADAPTER

cat > "$FIXTURE_ADAPTERS/fence-primary" <<'ADAPTER'
#!/usr/bin/env bash
set -u
root=$1
printf 'fence old-primary\n' >> "$root/events.log"
if [[ -e "$root/fail/fence" ]]; then
    exit 1
fi
: > "$root/state/fenced"
ADAPTER

cat > "$FIXTURE_ADAPTERS/activate-service" <<'ADAPTER'
#!/usr/bin/env bash
set -u
root=$1
service=$2
printf 'activate %s\n' "$service" >> "$root/events.log"
if [[ -e "$root/fail/activate-$service" ]]; then
    exit 1
fi
: > "$root/state/active/$service"
ADAPTER

cat > "$FIXTURE_ADAPTERS/check-health" <<'ADAPTER'
#!/usr/bin/env bash
set -u
root=$1
service=$2
printf 'health %s\n' "$service" >> "$root/events.log"
[[ -e "$root/state/active/$service" && ! -e "$root/fail/health-$service" ]]
ADAPTER

cat > "$FIXTURE_ADAPTERS/deactivate-service" <<'ADAPTER'
#!/usr/bin/env bash
set -u
root=$1
service=$2
printf 'deactivate %s\n' "$service" >> "$root/events.log"
if [[ -e "$root/fail/deactivate-$service" ]]; then
    exit 1
fi
rm -f -- "$root/state/active/$service"
ADAPTER

. "$PROJECT_ROOT/tests/fixture_lib.sh"

test_usage_is_rejected_without_touching_a_fixture() {
    local root="$TEST_TMP/usage"
    mkdir -p "$root"

    set +e
    bash "$PROJECT_ROOT/failover.sh" > "$root/stdout" 2> "$root/stderr"
    RUN_STATUS=$?
    set -e

    assert_status 64 'bad usage'
    assert_empty_file "$root/stdout" 'bad usage stdout'
    assert_lines "$root/stderr" 'usage: failover.sh SCENARIO_ROOT' 'bad usage stderr'
    assert_absent "$root/journal.log" 'bad usage does not create a journal'
}

test_invalid_fixture_is_rejected_before_journaling() {
    local root="$TEST_TMP/invalid"
    make_fixture "$root"
    chmod -x "$root/bin/check-health"

    run_failover "$root"
    assert_status 65 'invalid fixture'
    assert_empty_file "$root/stdout" 'invalid fixture stdout'
    assert_lines "$root/stderr" \
        'failover.sh: invalid scenario: missing adapter check-health' \
        'invalid fixture stderr'
    assert_absent "$root/journal.log" 'invalid fixture does not create a journal'
    assert_empty_file "$root/events.log" 'invalid fixture invokes no adapter'
}

test_successful_ordered_failover() {
    local root="$TEST_TMP/success"
    make_fixture "$root"
    printf 'STALE RECORD\n' > "$root/journal.log"

    run_failover "$root"
    assert_status 0 'successful failover'
    assert_empty_output "$root" 'successful failover'
    assert_exists "$root/state/fenced" 'successful failover fences source'
    assert_exists "$root/state/active/database" 'database activated'
    assert_exists "$root/state/active/cache" 'cache activated'
    assert_exists "$root/state/active/api" 'api activated'
    assert_lines "$root/events.log" 'precheck source-reachable
precheck replication-caught-up
precheck target-empty
fence old-primary
activate database
health database
activate cache
health cache
activate api
health api' 'successful adapter order'
    assert_lines "$root/journal.log" 'PRECHECK source-reachable
PRECHECK replication-caught-up
PRECHECK target-empty
FENCE old-primary
ACTIVATE database
HEALTH database
ACTIVATE cache
HEALTH cache
ACTIVATE api
HEALTH api
COMPLETE' 'successful journal'
}

test_precheck_stops_before_fence() {
    local root="$TEST_TMP/precheck"
    make_fixture "$root"
    : > "$root/fail/precheck-replication-caught-up"

    run_failover "$root"
    assert_status 65 'failed precheck'
    assert_empty_output "$root" 'failed precheck'
    assert_absent "$root/state/fenced" 'failed precheck does not fence'
    assert_absent "$root/state/active/database" 'failed precheck does not activate'
    assert_lines "$root/events.log" 'precheck source-reachable
precheck replication-caught-up' 'precheck fail-fast order'
    assert_lines "$root/journal.log" 'PRECHECK source-reachable
PRECHECK replication-caught-up
FAIL precheck:replication-caught-up' 'precheck failure journal'
}

test_fence_failure_stops_activation() {
    local root="$TEST_TMP/fence"
    make_fixture "$root"
    : > "$root/fail/fence"

    run_failover "$root"
    assert_status 66 'failed fence'
    assert_empty_output "$root" 'failed fence'
    assert_absent "$root/state/fenced" 'failed fence has no fenced marker'
    assert_absent "$root/state/active/database" 'failed fence does not activate'
    assert_lines "$root/events.log" 'precheck source-reachable
precheck replication-caught-up
precheck target-empty
fence old-primary' 'fence failure adapter order'
    assert_lines "$root/journal.log" 'PRECHECK source-reachable
PRECHECK replication-caught-up
PRECHECK target-empty
FENCE old-primary
FAIL fence:old-primary' 'fence failure journal'
}

test_activation_failure_rolls_back_prior_services() {
    local root="$TEST_TMP/activation"
    make_fixture "$root"
    : > "$root/fail/activate-cache"

    run_failover "$root"
    assert_status 67 'failed activation'
    assert_empty_output "$root" 'failed activation'
    assert_exists "$root/state/fenced" 'activation failure keeps source fenced'
    assert_absent "$root/state/active/database" 'prior service rolled back'
    assert_absent "$root/state/active/cache" 'failed activation not active'
    assert_absent "$root/state/active/api" 'later service not attempted'
    assert_lines "$root/events.log" 'precheck source-reachable
precheck replication-caught-up
precheck target-empty
fence old-primary
activate database
health database
activate cache
deactivate database' 'activation failure rollback order'
    assert_lines "$root/journal.log" 'PRECHECK source-reachable
PRECHECK replication-caught-up
PRECHECK target-empty
FENCE old-primary
ACTIVATE database
HEALTH database
ACTIVATE cache
FAIL activation:cache
ROLLBACK database
ABORT activation:cache' 'activation failure journal'
}

test_unhealthy_service_is_included_in_rollback() {
    local root="$TEST_TMP/health"
    make_fixture "$root"
    : > "$root/fail/health-cache"

    run_failover "$root"
    assert_status 68 'failed health check'
    assert_empty_output "$root" 'failed health check'
    assert_exists "$root/state/fenced" 'health failure keeps source fenced'
    assert_absent "$root/state/active/database" 'healthy predecessor rolled back'
    assert_absent "$root/state/active/cache" 'unhealthy activated service rolled back'
    assert_absent "$root/state/active/api" 'later service not attempted after health failure'
    assert_lines "$root/events.log" 'precheck source-reachable
precheck replication-caught-up
precheck target-empty
fence old-primary
activate database
health database
activate cache
health cache
deactivate cache
deactivate database' 'health failure reverse rollback order'
    assert_lines "$root/journal.log" 'PRECHECK source-reachable
PRECHECK replication-caught-up
PRECHECK target-empty
FENCE old-primary
ACTIVATE database
HEALTH database
ACTIVATE cache
HEALTH cache
FAIL health:cache
ROLLBACK cache
ROLLBACK database
ABORT health:cache' 'health failure journal'
}

test_rollback_failure_does_not_skip_earlier_service() {
    local root="$TEST_TMP/rollback"
    make_fixture "$root"
    : > "$root/fail/health-api"
    : > "$root/fail/deactivate-cache"

    run_failover "$root"
    assert_status 69 'failed rollback adapter'
    assert_empty_output "$root" 'failed rollback adapter'
    assert_absent "$root/state/active/api" 'unhealthy service rollback attempted'
    assert_exists "$root/state/active/cache" 'failed cache rollback remains observable'
    assert_absent "$root/state/active/database" 'rollback continues after cache failure'
    assert_lines "$root/events.log" 'precheck source-reachable
precheck replication-caught-up
precheck target-empty
fence old-primary
activate database
health database
activate cache
health cache
activate api
health api
deactivate api
deactivate cache
deactivate database' 'rollback attempts every activated service'
    assert_lines "$root/journal.log" 'PRECHECK source-reachable
PRECHECK replication-caught-up
PRECHECK target-empty
FENCE old-primary
ACTIVATE database
HEALTH database
ACTIVATE cache
HEALTH cache
ACTIVATE api
HEALTH api
FAIL health:api
ROLLBACK api
ROLLBACK cache
ROLLBACK_FAILED cache
ROLLBACK database
ABORT health:api' 'rollback failure journal'
}

tests=(
    test_usage_is_rejected_without_touching_a_fixture
    test_invalid_fixture_is_rejected_before_journaling
    test_successful_ordered_failover
    test_precheck_stops_before_fence
    test_fence_failure_stops_activation
    test_activation_failure_rolls_back_prior_services
    test_unhealthy_service_is_included_in_rollback
    test_rollback_failure_does_not_skip_earlier_service
)

for test_name in "${tests[@]}"; do
    "$test_name"
    printf 'ok - %s\n' "$test_name"
done

printf '1..%s\n' "${#tests[@]}"
