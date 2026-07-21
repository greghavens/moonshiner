#!/usr/bin/env bash

set -u

IFS= read -r state < state/service.state
printf 'service=api state=%s\n' "$state"
