#!/bin/sh
set -eu

has_session_definition() {
    tr '\n' ' ' < "$1" |
        grep -Eq 'struct[[:space:]]+session[[:space:]]*\{'
}

if ! has_session_definition src/session.c; then
    echo "FAIL: struct session is not defined in src/session.c" >&2
    exit 1
fi

find . -type f \( -name '*.c' -o -name '*.h' \) \
    ! -path './src/session.c' -print |
while IFS= read -r source_file; do
    if has_session_definition "$source_file"; then
        echo "FAIL: struct session is also defined in $source_file" >&2
        exit 1
    fi
done

require_calls() {
    source_file=$1
    shift
    flattened=$(tr '\n' ' ' < "$source_file")
    for function_name do
        if ! printf '%s\n' "$flattened" |
            grep -Eq "(^|[^[:alnum:]_])${function_name}[[:space:]]*\\("; then
            echo "FAIL: $source_file does not call $function_name" >&2
            exit 1
        fi
    done
}

require_calls src/session_adapter.c session_get_phase session_id \
    session_rx_bytes session_tx_bytes session_peer
require_calls src/session_pump.c session_get_phase session_record_traffic \
    session_set_phase

echo "ok: private definition and internal consumers are isolated"
