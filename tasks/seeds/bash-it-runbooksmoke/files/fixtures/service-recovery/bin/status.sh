#!/usr/bin/env bash

set -u

IFS='=' read -r service_key service < config/service.env
IFS='=' read -r state_key state < state/service.state

[[ $service_key == 'SERVICE' && $state_key == 'STATE' ]] || exit 2
printf 'service=%s\nstate=%s\n' "$service" "$state"
