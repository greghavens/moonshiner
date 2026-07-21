#!/usr/bin/env bash

set -u
set -o pipefail

usage() {
    printf 'usage: failover.sh SCENARIO_ROOT\n' >&2
}

if (( $# != 1 )); then
    usage
    exit 64
fi

scenario=$1
services_file="$scenario/services.list"
journal="$scenario/journal.log"

adapters=(
    precheck
    fence-primary
    activate-service
    check-health
    deactivate-service
)

if [[ ! -d "$scenario" || ! -r "$services_file" ]]; then
    printf 'failover.sh: invalid scenario: %s\n' "$scenario" >&2
    exit 65
fi

for adapter in "${adapters[@]}"; do
    if [[ ! -x "$scenario/bin/$adapter" ]]; then
        printf 'failover.sh: invalid scenario: missing adapter %s\n' "$adapter" >&2
        exit 65
    fi
done

mapfile -t services < "$services_file"
if (( ${#services[@]} == 0 )); then
    printf 'failover.sh: invalid scenario: empty services.list\n' >&2
    exit 65
fi
for service in "${services[@]}"; do
    if [[ -z "$service" || "$service" == */* || "$service" == .* ]]; then
        printf 'failover.sh: invalid service name: %s\n' "$service" >&2
        exit 65
    fi
done

: > "$journal"

journal_record() {
    printf '%s\n' "$1" >> "$journal"
}

activated=()

abort_with_rollback() {
    local reason=$1
    local original_status=$2
    local rollback_failed=0
    local index service

    for (( index=${#activated[@]} - 1; index >= 0; index-- )); do
        service=${activated[index]}
        journal_record "ROLLBACK $service"
        if ! "$scenario/bin/deactivate-service" "$scenario" "$service"; then
            journal_record "ROLLBACK_FAILED $service"
            rollback_failed=1
        fi
    done

    journal_record "ABORT $reason"
    if (( rollback_failed )); then
        exit 69
    fi
    exit "$original_status"
}

prechecks=(source-reachable replication-caught-up target-empty)
for check in "${prechecks[@]}"; do
    journal_record "PRECHECK $check"
    if ! "$scenario/bin/precheck" "$scenario" "$check"; then
        journal_record "FAIL precheck:$check"
        exit 65
    fi
done

journal_record 'FENCE old-primary'
if ! "$scenario/bin/fence-primary" "$scenario"; then
    journal_record 'FAIL fence:old-primary'
    exit 66
fi

for service in "${services[@]}"; do
    journal_record "ACTIVATE $service"
    if ! "$scenario/bin/activate-service" "$scenario" "$service"; then
        journal_record "FAIL activation:$service"
        abort_with_rollback "activation:$service" 67
    fi

    journal_record "HEALTH $service"
    if ! "$scenario/bin/check-health" "$scenario" "$service"; then
        journal_record "FAIL health:$service"
        abort_with_rollback "health:$service" 68
    fi

    activated+=("$service")
done

journal_record 'COMPLETE'
exit 0
