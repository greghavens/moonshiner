#!/usr/bin/env bash

# A local-only remote-support session simulator. Action names are dispatched
# below; input is never evaluated as shell source.

declare -A ALLOWED=()
TICKET_ID=
AUDIT_LOG=
RUNTIME_DIR=
SESSION_DIR=
SESSION_ACTIVE=0
ACTION_NUMBER=0

usage() {
    printf '%s\n' 'usage: remote_support.sh <ticket-file> <consent-file> <actions-file> <audit-log> <runtime-dir>' >&2
}

audit() {
    printf '%s\n' "$1" >>"$AUDIT_LOG"
}

load_ticket() {
    local file=$1 key value
    local seen_ticket=0 allow_count=0

    while IFS='=' read -r key value || [[ -n $key || -n $value ]]; do
        case $key in
            ticket)
                if (( seen_ticket )) || [[ ! $value =~ ^[A-Z]+-[0-9]+$ ]]; then
                    return 1
                fi
                TICKET_ID=$value
                seen_ticket=1
                ;;
            allow)
                if [[ ! $value =~ ^[a-z][a-z-]*$ ]]; then
                    return 1
                fi
                ALLOWED["$value"]=1
                ((allow_count += 1))
                ;;
            *)
                return 1
                ;;
        esac
    done <"$file"

    (( seen_ticket == 1 && allow_count > 0 ))
}

has_explicit_consent() {
    local file=$1 key value
    local consent_ticket= consent_value= fields=0

    while IFS='=' read -r key value || [[ -n $key || -n $value ]]; do
        case $key in
            ticket)
                [[ -z $consent_ticket ]] || return 1
                consent_ticket=$value
                ;;
            consent)
                [[ -z $consent_value ]] || return 1
                consent_value=$value
                ;;
            *)
                return 1
                ;;
        esac
        ((fields += 1))
    done <"$file"

    [[ $fields -eq 2 && $consent_ticket == "$TICKET_ID" && $consent_value == yes ]]
}

redact_output() {
    sed -E 's/((api_key|password|token|secret)=)[^[:space:]]+/\1[REDACTED]/g'
}

run_builtin() {
    local action=$1
    case $action in
        status)
            printf '%s\n' 'service=ready'
            ;;
        diagnostics)
            printf '%s\n' 'host=demo api_key=demo-api password=demo-pass token=demo-token'
            ;;
        fail)
            printf '%s\n' 'diagnostic failed secret=internal-detail'
            return 23
            ;;
        hold)
            : >"$SESSION_DIR/ready"
            while :; do
                sleep 0.05
            done
            ;;
        *)
            printf 'remote_support.sh: unsupported built-in action: %s\n' "$action" >&2
            return 64
            ;;
    esac
}

finish_session() {
    local status=$1 reason=$2
    (( SESSION_ACTIVE )) || return 0

    audit "STOP ticket=$TICKET_ID reason=$reason rc=$status"
    rm -f -- "$SESSION_DIR"/raw.* "$SESSION_DIR/active" "$SESSION_DIR/ready"
    rmdir -- "$SESSION_DIR"
    SESSION_ACTIVE=0
}

disconnect() {
    local status=$1
    trap - INT TERM
    finish_session "$status" disconnected
    exit "$status"
}

run_actions() {
    local file=$1 action raw output result status

    while IFS= read -r action || [[ -n $action ]]; do
        [[ -z $action || $action == \#* ]] && continue
        ((ACTION_NUMBER += 1))
        audit "ACTION ticket=$TICKET_ID command=$action"

        if [[ ! $action =~ ^[a-z][a-z-]*$ || ! ${ALLOWED[$action]+present} ]]; then
            audit "RESULT ticket=$TICKET_ID command=$action result=denied rc=126 output="
            printf 'remote_support.sh: action not allowed by ticket: %s\n' "$action" >&2
            return 126
        fi

        raw=$SESSION_DIR/raw.$ACTION_NUMBER
        if run_builtin "$action" >"$raw" 2>&1; then
            status=0
            result=ok
        else
            status=$?
            result=failed
        fi

        output=$(redact_output <"$raw")
        output=${output//$'\n'/ | }
        [[ -z $output ]] || printf '%s\n' "$output"
        audit "RESULT ticket=$TICKET_ID command=$action result=$result rc=$status output=$output"
        rm -f -- "$raw"

        (( status == 0 )) || return "$status"
    done <"$file"
}

main() {
    if (( $# != 5 )); then
        usage
        return 64
    fi

    local ticket_file=$1 consent_file=$2 actions_file=$3
    AUDIT_LOG=$4
    RUNTIME_DIR=$5

    if [[ ! -r $ticket_file || ! -r $consent_file || ! -r $actions_file ]]; then
        printf '%s\n' 'remote_support.sh: input file is not readable' >&2
        return 66
    fi
    if ! load_ticket "$ticket_file"; then
        printf '%s\n' 'remote_support.sh: invalid ticket' >&2
        return 65
    fi
    if ! : >>"$AUDIT_LOG"; then
        printf '%s\n' 'remote_support.sh: cannot append audit log' >&2
        return 73
    fi
    if ! has_explicit_consent "$consent_file"; then
        audit "DENIED ticket=$TICKET_ID reason=consent"
        printf 'remote_support.sh: explicit consent required for %s\n' "$TICKET_ID" >&2
        return 77
    fi

    if ! mkdir -p -- "$RUNTIME_DIR"; then
        printf '%s\n' 'remote_support.sh: cannot create runtime directory' >&2
        return 73
    fi
    SESSION_DIR=$RUNTIME_DIR/session-$TICKET_ID
    if ! mkdir -- "$SESSION_DIR" 2>/dev/null; then
        printf 'remote_support.sh: session already active for %s\n' "$TICKET_ID" >&2
        return 73
    fi
    : >"$SESSION_DIR/active"
    SESSION_ACTIVE=1
    trap 'disconnect 130' INT
    trap 'disconnect 143' TERM
    audit "START ticket=$TICKET_ID"

    local status=0
    run_actions "$actions_file" || status=$?
    if (( status != 0 )); then
        return "$status"
    fi

    finish_session 0 completed
    return 0
}

main "$@"
