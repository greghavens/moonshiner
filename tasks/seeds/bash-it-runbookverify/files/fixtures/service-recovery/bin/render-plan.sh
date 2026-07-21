#!/usr/bin/env bash

set -u

IFS= read -r state < state/service.state
IFS= read -r replicas < config/replicas.conf
printf 'check api\nstate %s\nreplicas %s\n' "$state" "$replicas"
