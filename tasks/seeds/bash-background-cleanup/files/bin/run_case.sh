#!/usr/bin/env bash

# Run one case in the background so that the harness can relay cancellation.
set -u

if (( $# == 0 )); then
    printf 'usage: %s COMMAND [ARG ...]\n' "${0##*/}" >&2
    exit 64
fi

child_pid=

cleanup() {
    local status=$?

    if [[ -n ${child_pid:-} ]]; then
        kill "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi

    return "$status"
}

trap cleanup EXIT HUP INT TERM

"$@" <&0 &
child_pid=$!
wait "$child_pid"
