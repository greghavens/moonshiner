#!/usr/bin/env bash

set -u

trap 'rm -f -- .plan.tmp' EXIT HUP INT TERM
IFS='=' read -r service_key service < config/service.env
IFS='=' read -r state_key state < state/service.state
[[ $service_key == 'SERVICE' && $state_key == 'STATE' ]] || exit 2

printf 'service=%s action=observe current=%s\n' "$service" "$state" > .plan.tmp
cp -- .plan.tmp /dev/stdout
